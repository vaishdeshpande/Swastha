"""Agent 3 — Appointment Scheduler.

Books, reschedules, or cancels OPD appointments. Both the inbound
(patient-initiated) and outbound (cron-driven confirmation call) flows live
here, sharing the same tools.
"""

import logging
import os

from langsmith import traceable
from sarvamai import SarvamAI

from agents.prompts.scheduler import KNOWN_DEPARTMENTS, build_scheduler_prompt, normalize_department
from agents.state import AgentState
from agents.tools.db_tools import (
    book_slot,
    cancel_appointment,
    check_available_slots,
    confirm_appointment,
    get_next_available,
)
from agents.tools.llm_json import extract_json
from agents.tools.redis_tools import get_cached_slots
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

# Signals that mean the first scheduler turn is NOT a plain "show me slots"
# request, so the LLM must decide the action. Everything else on a first
# scheduler turn is deterministic per the prompt's own Step 1 rule
# ("NO slots offered yet → always check_slots with date=any"), and skipping
# the LLM there saves one full sarvam-30b round trip (~2-14s measured).
_CANCEL_RESCHEDULE_SIGNALS = [
    "cancel", "कैंसिल", "रद्द", "reschedule", "postpone", "बदलना", "बदलायच",
    "cancle", "टालना", "पुढे ढकल",
]
_DATE_SIGNALS = [
    "kal", "कल", "aaj", "आज", "parso", "परसों", "उद्या", "परवा", "tarikh", "तारीख",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "सोमवार", "मंगलवार", "मंगळवार", "बुधवार", "गुरुवार", "गुरूवार", "शुक्रवार",
    "शनिवार", "रविवार", "tomorrow", "today",
]

_DEPARTMENT_LABELS: dict[str, str] = {
    "general": "General Physician",
    "cardiology": "Cardiology (Heart)",
    "ortho": "Orthopaedics (Bone & Joint)",
    "pediatrics": "Paediatrics (Child)",
    "dermatology": "Dermatology (Skin)",
    "gynecology": "Gynaecology (Women's Health)",
    "neurology": "Neurology (Brain & Nerves)",
    "ent": "ENT (Ear, Nose & Throat)",
    "ophthalmology": "Ophthalmology (Eye)",
    "psychiatry": "Psychiatry (Mental Health)",
    "oncology": "Oncology (Cancer)",
    "nephrology": "Nephrology (Kidney)",
    "endocrinology": "Endocrinology (Hormones)",
    "gastroenterology": "Gastroenterology (Stomach & Gut)",
    "pulmonology": "Pulmonology (Lungs & Breathing)",
}


def _first_turn_fast_path(state: AgentState, entered_from: str) -> bool:
    """True when the scheduler can deterministically confirm the department
    without an LLM call. Fires on the very first scheduler turn (from
    voice_intake, no slots offered, no prior department confirmation).

    The response is fully predictable — we already know the department and
    can build the confirmation message from _DEPARTMENT_LABELS alone.
    Any subsequent turn (patient replied, cancel/reschedule, date named)
    goes through the LLM."""
    if entered_from != "voice_intake":
        return False
    if state.get("offered_slots"):
        return False
    if state.get("appointment_id"):
        return False
    if state.get("department_confirmed"):
        return False  # already confirmed — don't re-ask
    last_user = next(
        (m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"),
        "",
    ).lower()
    if any(sig in last_user for sig in _CANCEL_RESCHEDULE_SIGNALS):
        return False
    if any(sig in last_user for sig in _DATE_SIGNALS):
        return False
    return True


@traceable(run_type="llm", name="sarvam-30b:scheduler_decision")
async def _decide_scheduler_action(state: AgentState) -> dict:
    """Calls sarvam-30b to decide the next scheduling action from the
    conversation so far. Returns the parsed JSON decision dict."""
    system_prompt = build_scheduler_prompt(
        lang_code=state["lang_code"],
        department=state.get("department"),
        offered_slots=state.get("offered_slots"),
        department_confirmed=state.get("department_confirmed"),
        hospital_availability=state.get("hospital_availability"),
    )
    messages = [{"role": "system", "content": system_prompt}, *state["messages"]]

    def _sync_call() -> str:
        # temperature=0: structured JSON decision, not creative text.
        # max_tokens is a runaway guard only — sarvam-30b spends ~300-400
        # hidden reasoning tokens before content, so a tight cap would
        # truncate the answer to nothing (verified empirically).
        r = client.chat.completions(
            messages=messages, model="sarvam-30b", temperature=0.0, max_tokens=2048
        )
        return r.choices[0].message.content or ""

    import asyncio as _asyncio
    try:
        reply = await _asyncio.wait_for(_asyncio.to_thread(_sync_call), timeout=20.0)
    except _asyncio.TimeoutError:
        logger.error("scheduler: LLM decision timed out — falling back to clarify")
        return {"action": "clarify", "reply": None, "distress": False}
    parsed = extract_json(reply)
    if parsed is None:
        logger.warning("scheduler: LLM reply wasn't parseable JSON, treating as clarification: %r", reply)
        # Never let raw JSON syntax reach TTS — clarify with reply=None so the
        # node generates a clean confirmation question instead.
        if "{" in reply or '"' in reply:
            from agents.tools.llm_json import extract_reply_text
            reply = extract_reply_text(reply)
        return {"action": "clarify", "reply": reply, "distress": False}
    return parsed


def _format_slot_options(slots: list[dict]) -> str:
    lines = [f"- {s['doctor_name']}, {s['department']}, {s.get('date', '')}, {s['time']}" for s in slots]
    return "\n".join(lines)


async def scheduler_node(state: AgentState) -> AgentState:
    entered_from = state.get("current_agent", "")  # who ran before us in this pass
    state["current_agent"] = "scheduler"
    logger.info("scheduler: start (call_id=%s, department=%s)", state.get("call_id"), state.get("department"))

    if _first_turn_fast_path(state, entered_from):
        # Deterministic: confirm department before showing slots — no LLM needed.
        logger.info("scheduler: first-turn fast path — confirm_department without LLM")
        decision = {"action": "confirm_department", "date": "any", "chosen_slot_id": None,
                    "cancel_appointment_id": None, "distress": False, "reply": None}
    else:
        decision = await _decide_scheduler_action(state)
    lang_code = state["lang_code"]
    messages = [*state["messages"]]

    if decision.get("distress"):
        logger.warning("scheduler: escalating — patient appeared confused/distressed (call_id=%s)", state.get("call_id"))
        return {
            **state,
            "escalation_required": True,
            "escalation_reason": "Patient appeared confused or distressed during scheduling",
        }

    action = decision.get("action")
    logger.info("scheduler: decided action=%s", action)

    if action == "clarify":
        reply = decision.get("reply")
        if not reply:
            # LLM returned clarify with no text. If department not yet confirmed,
            # re-ask confirmation. Otherwise ask the patient to choose a slot.
            dept = state.get("department", "general")
            dept_label = _DEPARTMENT_LABELS.get(dept, dept)
            if not state.get("department_confirmed"):
                reply_en = f"We have a {dept_label} available. Would you like to book an appointment with them?"
            elif state.get("offered_slots"):
                reply_en = "Which slot would you prefer? Please choose from the options I mentioned."
            else:
                reply_en = f"Shall I check the available slots for the {dept_label}?"
            try:
                reply = await translate_text(reply_en, source_lang="en-IN", target_lang=lang_code)
            except Exception:
                reply = reply_en
        elif lang_code not in ("hi-IN", "en-IN"):
            try:
                reply = await translate_text(reply, "hi-IN", lang_code)
            except Exception:
                logger.exception("scheduler: clarify translation failed, using original")
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages}

    if action == "confirm_department":
        # First scheduler turn: tell patient which department we found and ask if
        # that's what they want. Slots are already pre-fetched in Redis from
        # voice_intake (Scenario 2) so waiting for the patient's "haan" costs nothing.
        dept = normalize_department(state.get("department") or "general")
        if dept == "unknown":
            dept = "general"
        dept_label = _DEPARTMENT_LABELS.get(dept, dept)
        confirm_en = (
            f"We have a {dept_label} available for you. "
            f"Shall I check the available appointment slots?"
        )
        reply = await translate_text(confirm_en, source_lang="en-IN", target_lang=lang_code)
        messages.append({"role": "assistant", "content": reply})
        logger.info("scheduler: asked department confirmation for dept=%s", dept)
        return {**state, "messages": messages, "department": dept}

    if action == "check_slots":
        date = decision.get("date") or "any"

        # Patient may have specified a different department — LLM sets decision["department"].
        requested_dept = decision.get("department")
        if requested_dept:
            raw_dept = requested_dept
            dept = normalize_department(raw_dept)
            logger.info("scheduler: patient requested dept change %r → %r", raw_dept, dept)
        else:
            raw_dept = state.get("department") or "general"
            dept = normalize_department(raw_dept)
            if dept != raw_dept:
                logger.info("scheduler: normalized department %r → %r", raw_dept, dept)

        state = {**state, "department": dept if dept != "unknown" else "general",
                 "department_confirmed": True}

        # Unknown department — fall back to general physician and let patient know.
        if dept == "unknown":
            logger.info("scheduler: unknown department=%r — offering general physician", raw_dept)
            unknown_msg_en = (
                f"I'm sorry, we don't have a {raw_dept} department at Apollo Hospitals. "
                "However, our General Physician can evaluate you and refer you to the right specialist. "
                "Would you like me to book an appointment with our General Physician?"
            )
            reply = await translate_text(unknown_msg_en, source_lang="en-IN", target_lang=lang_code)
            messages.append({"role": "assistant", "content": reply})
            return {**state, "messages": messages, "department": "general"}

        # Scenario 2: read pre-fetched cache written by voice_intake (sub-10ms vs Supabase).
        # Use resolved `dept` — state["department"] may lag if dept was just changed.
        slots = await get_cached_slots(dept, date)
        if slots is not None:
            logger.info("scheduler: slot cache HIT for department=%s date=%s (%d slot(s))", dept, date, len(slots))
        else:
            slots = await check_available_slots(dept, date)
            if not slots:
                logger.info("scheduler: no slots for department=%s date=%s, checking next available", dept, date)
                slots = await get_next_available(dept, 3)
            else:
                slots = slots[:3]
        logger.info("scheduler: offering %d slot(s)", len(slots))

        options_en = _format_slot_options(slots)
        reply_en = f"Here are the available slots:\n{options_en}" if slots else "No slots are available right now."
        reply = await translate_text(reply_en, source_lang="en-IN", target_lang=lang_code)
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages, "offered_slots": slots}

    if action == "confirm_booking" and not state.get("offered_slots"):
        # Patient said "confirm" before seeing any slots — they're confused or
        # jumped ahead. Show them slots instead of going silent.
        logger.info(
            "scheduler: confirm_booking with no offered_slots — redirecting to check_slots (call_id=%s)",
            state.get("call_id"),
        )
        dept = normalize_department(state.get("department") or "general")
        if dept == "unknown":
            dept = "general"
        slots = await get_cached_slots(dept, "any") or await check_available_slots(dept, "any") or await get_next_available(dept, 3)
        slots = slots[:3]
        redirect_en = "Let me first show you the available slots so you can choose one."
        redirect = await translate_text(redirect_en, source_lang="en-IN", target_lang=lang_code)
        options_en = _format_slot_options(slots)
        slots_reply = await translate_text(
            f"Here are the available slots:\n{options_en}" if slots else "No slots are available right now.",
            source_lang="en-IN", target_lang=lang_code,
        )
        messages.append({"role": "assistant", "content": f"{redirect} {slots_reply}"})
        return {**state, "messages": messages, "offered_slots": slots, "department_confirmed": True}

    if action == "confirm_booking":
        # ── Defense-in-depth: department must be confirmed before any booking ──
        # Layer 1: _first_turn_fast_path ensures confirm_department fires on turn 1.
        # Layer 2 (this guard): blocks booking even if layer 1 is bypassed for any reason.
        if not state.get("department_confirmed"):
            logger.warning(
                "scheduler: BLOCKED confirm_booking — department_confirmed=False (call_id=%s). "
                "Redirecting to confirm_department.",
                state.get("call_id"),
            )
            dept = normalize_department(state.get("department") or "general")
            if dept == "unknown":
                dept = "general"
            dept_label = _DEPARTMENT_LABELS.get(dept, dept)
            confirm_en = (
                f"We have a {dept_label} available for you. "
                f"Shall I check the available appointment slots?"
            )
            reply = await translate_text(confirm_en, source_lang="en-IN", target_lang=lang_code)
            messages.append({"role": "assistant", "content": reply})
            return {**state, "messages": messages, "department": dept}

        # ── Confidence check: low-confidence slot match → ask explicitly ──
        confidence = decision.get("confidence", 1.0)
        slot_id = decision.get("chosen_slot_id")
        if confidence < 0.60 or not slot_id:
            offered = state.get("offered_slots") or []
            if offered:
                options = " / ".join(
                    f"{s.get('time', '')} ({s.get('doctor_name', '')})" for s in offered
                )
                clarify_en = f"Just to confirm — which slot would you prefer? {options}"
            else:
                clarify_en = "Which slot would you like to book?"
            reply = await translate_text(clarify_en, source_lang="en-IN", target_lang=lang_code)
            messages.append({"role": "assistant", "content": reply})
            logger.info(
                "scheduler: low-confidence slot selection (%.2f) — asking for explicit choice (call_id=%s)",
                confidence, state.get("call_id"),
            )
            return {**state, "messages": messages}

        slot_id = decision.get("chosen_slot_id")
        logger.info("scheduler: booking slot_id=%s for patient_id=%s", slot_id, state.get("patient_id"))
        # Capture slot details before offered_slots is cleared — livekit_agent
        # uses this for the booking_confirmed UI card (doctor name, time, date).
        offered = state.get("offered_slots") or []
        matched_slot = next((s for s in offered if s.get("slot_id") == slot_id), {})
        confirmation = await book_slot(state["patient_id"], slot_id)
        logger.info("scheduler: booked appointment_id=%s", confirmation["appointment_id"])
        reply_en = (
            f"Your appointment with {confirmation['doctor_name']} is confirmed "
            f"on {confirmation['date']} at {confirmation['time']}."
        )
        reply = await translate_text(reply_en, source_lang="en-IN", target_lang=lang_code)
        messages.append({"role": "assistant", "content": reply})
        return {
            **state,
            "messages": messages,
            "offered_slots": None,
            "appointment_id": confirmation["appointment_id"],
            "booked_slot_details": {
                "doctor_name": matched_slot.get("doctor_name") or confirmation.get("doctor_name", ""),
                "department": state.get("department", "general"),
                "date": matched_slot.get("date") or confirmation.get("date", ""),
                "time": matched_slot.get("time") or confirmation.get("time", ""),
            },
        }

    if action == "cancel":
        appointment_id = decision.get("cancel_appointment_id")
        logger.info("scheduler: cancelling appointment_id=%s", appointment_id)
        await cancel_appointment(appointment_id)
        reply = await translate_text(
            "Your appointment has been cancelled.", source_lang="en-IN", target_lang=lang_code
        )
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages, "appointment_id": None}

    if action == "reschedule":
        appointment_id = decision.get("cancel_appointment_id")
        logger.info("scheduler: rescheduling — cancelling appointment_id=%s first", appointment_id)
        await cancel_appointment(appointment_id)
        slots = await get_next_available(state["department"], 3) if not decision.get("date") else (
            await check_available_slots(state["department"], decision["date"]) or
            await get_next_available(state["department"], 3)
        )
        logger.info("scheduler: offering %d slot(s) for reschedule", len(slots))
        options_en = _format_slot_options(slots)
        reply = await translate_text(
            f"Let's find you a new slot:\n{options_en}", source_lang="en-IN", target_lang=lang_code
        )
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages, "offered_slots": slots, "appointment_id": None}

    # Unknown action — always produce a spoken reply so the patient never hears silence.
    logger.warning("scheduler: unrecognized action=%r, falling back to clarify loop", action)
    fallback_en = "I want to make sure I help you correctly. Would you like to book an appointment or choose from the available slots?"
    try:
        fallback = await translate_text(fallback_en, source_lang="en-IN", target_lang=lang_code)
    except Exception:
        logger.exception("scheduler: fallback translation failed — using English")
        fallback = fallback_en
    messages.append({"role": "assistant", "content": fallback})
    return {**state, "messages": messages}


async def scheduler_outbound_node(state: AgentState) -> AgentState:
    """Cron-driven appointment confirmation call.

    Script: "Namaste, aapka appointment Dr. X ke saath kal subah 10 baje hai.
    Kya aap aa rahe hain?" — if the patient says no, offer to reschedule
    using the same slot-checking logic as the inbound flow.
    """
    state["current_agent"] = "scheduler_outbound"
    lang_code = state["lang_code"]
    logger.info("scheduler_outbound: start (call_id=%s, appointment_id=%s)", state.get("call_id"), state.get("appointment_id"))

    # The confirmation job row carries no appointment reference — resolve the
    # patient's latest booked appointment here. If there's nothing to confirm
    # (booking was cancelled, or the job is a stale duplicate), close the job
    # WITHOUT spending an LLM call — this path used to crash with KeyError and
    # retry every 30 minutes forever.
    appointment_id = state.get("appointment_id")
    if not appointment_id:
        from agents.tools.db_tools import get_latest_booked_appointment
        appointment_id = await get_latest_booked_appointment(state["patient_id"])
    if not appointment_id:
        logger.warning(
            "scheduler_outbound: no booked appointment for patient_id=%s — closing job without call",
            state.get("patient_id"),
        )
        return {**state, "call_outcome": {"confirmed": False, "reason": "no booked appointment on file"}}

    script_en = "This is a reminder call about your upcoming appointment tomorrow. Will you be attending?"
    script = await translate_text(script_en, source_lang="en-IN", target_lang=lang_code)
    messages = [*state["messages"], {"role": "assistant", "content": script}]

    decision = await _decide_scheduler_action({**state, "messages": messages})

    if decision.get("action") in ("cancel", "reschedule") or decision.get("distress"):
        logger.info("scheduler_outbound: patient declined/rescheduled, handing off to inbound scheduler flow")
        return await scheduler_node({**state, "messages": messages})

    logger.info("scheduler_outbound: confirming appointment_id=%s", appointment_id)
    await confirm_appointment(appointment_id)
    closing = await translate_text(
        "Thank you, your appointment is confirmed. See you then.", source_lang="en-IN", target_lang=lang_code
    )
    messages.append({"role": "assistant", "content": closing})
    return {**state, "messages": messages, "call_outcome": {"confirmed": True}}
