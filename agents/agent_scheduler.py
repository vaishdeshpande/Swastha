"""Agent 3 — Appointment Scheduler.

Books, reschedules, or cancels OPD appointments. Both the inbound
(patient-initiated) and outbound (cron-driven confirmation call) flows live
here, sharing the same tools.
"""

import logging
import os

from langsmith import traceable
from sarvamai import SarvamAI

from agents.prompts.scheduler import build_scheduler_prompt
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


def _first_turn_fast_path(state: AgentState, entered_from: str) -> bool:
    """True when the scheduler can skip the LLM because the decision is
    deterministic per the prompt's own Step 1 rule (no slots offered yet →
    check_slots with date=any).

    Only fires when the scheduler was entered in the SAME graph pass as
    voice_intake (entered_from=="voice_intake"): intake's LLM just read this
    utterance and judged it a clean booking intent, so distress/cancel/clarify
    have effectively been screened this turn. Any direct entry (patient
    replying to a scheduler question, outbound flow) still gets the LLM."""
    if entered_from != "voice_intake":
        return False
    if state.get("offered_slots"):
        return False  # slot matching ("pehla wala", "do baje") needs the LLM
    if state.get("appointment_id"):
        return False  # existing appointment in play — cancel/reschedule needs the LLM
    last_user = next(
        (m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"),
        "",
    ).lower()
    if any(sig in last_user for sig in _CANCEL_RESCHEDULE_SIGNALS):
        return False
    if any(sig in last_user for sig in _DATE_SIGNALS):
        return False  # patient named a date — LLM must extract it
    return True


@traceable(run_type="llm", name="sarvam-30b:scheduler_decision")
async def _decide_scheduler_action(state: AgentState) -> dict:
    """Calls sarvam-30b to decide the next scheduling action from the
    conversation so far. Returns the parsed JSON decision dict."""
    system_prompt = build_scheduler_prompt(
        lang_code=state["lang_code"],
        department=state.get("department"),
        offered_slots=state.get("offered_slots"),
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
    reply = await _asyncio.to_thread(_sync_call)
    parsed = extract_json(reply)
    if parsed is None:
        logger.warning("scheduler: LLM reply wasn't parseable JSON, treating as clarification: %r", reply)
        return {"action": "clarify", "reply": reply, "distress": False}
    return parsed


def _format_slot_options(slots: list[dict]) -> str:
    lines = [f"- {s['doctor_name']}, {s['department']}, {s['time']}" for s in slots]
    return "\n".join(lines)


async def scheduler_node(state: AgentState) -> AgentState:
    entered_from = state.get("current_agent", "")  # who ran before us in this pass
    state["current_agent"] = "scheduler"
    logger.info("scheduler: start (call_id=%s, department=%s)", state.get("call_id"), state.get("department"))

    if _first_turn_fast_path(state, entered_from):
        # Deterministic per prompt Step 1 — no LLM call needed.
        logger.info("scheduler: first-turn fast path — check_slots(date=any) without LLM")
        decision = {"action": "check_slots", "date": "any", "chosen_slot_id": None,
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
            # LLM returned clarify with no reply text — generate a department confirmation.
            dept = state.get("department", "general")
            reply = f"Aapke liye {dept} specialist se appointment book karein? Kya yeh theek hai?"
        if lang_code not in ("hi-IN", "en-IN"):
            try:
                reply = await translate_text(reply, "hi-IN", lang_code)
            except Exception:
                logger.exception("scheduler: clarify translation failed, using original")
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages}

    if action == "check_slots":
        date = decision.get("date") or "any"

        # Scenario 2: read pre-fetched cache written by voice_intake (sub-10ms vs Supabase)
        slots = await get_cached_slots(state["department"], date)
        if slots is not None:
            logger.info("scheduler: slot cache HIT for department=%s date=%s (%d slot(s))", state["department"], date, len(slots))
        else:
            slots = await check_available_slots(state["department"], date)
            if not slots:
                logger.info("scheduler: no slots for department=%s date=%s, checking next available", state["department"], date)
                slots = await get_next_available(state["department"], 3)
            else:
                slots = slots[:3]
        logger.info("scheduler: offering %d slot(s)", len(slots))

        options_en = _format_slot_options(slots)
        reply_en = f"Here are the available slots:\n{options_en}" if slots else "No slots are available right now."
        reply = await translate_text(reply_en, source_lang="en-IN", target_lang=lang_code)
        messages.append({"role": "assistant", "content": reply})
        return {**state, "messages": messages, "offered_slots": slots}

    if action == "confirm_booking":
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

    # Unknown action — fall back to a clarifying loop rather than crash the call.
    logger.warning("scheduler: unrecognized action=%r, falling back to clarify loop", action)
    fallback_hi = "Kya aap appointment book karna chahte hain?"
    try:
        fallback = await translate_text(fallback_hi, "hi-IN", lang_code) if lang_code not in ("hi-IN", "en-IN") else fallback_hi
    except Exception:
        fallback = fallback_hi
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
