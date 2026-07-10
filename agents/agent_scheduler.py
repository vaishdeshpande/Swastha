"""Agent 3 — Appointment Scheduler.

Books, reschedules, or cancels OPD appointments. Both the inbound
(patient-initiated) and outbound (cron-driven confirmation call) flows live
here, sharing the same tools.
"""

import logging
import os

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


async def _decide_scheduler_action(state: AgentState) -> dict:
    """Calls sarvam-30b to decide the next scheduling action from the
    conversation so far. Returns the parsed JSON decision dict."""
    system_prompt = build_scheduler_prompt(
        lang_code=state["lang_code"],
        department=state.get("department"),
        offered_slots=state.get("offered_slots"),
    )
    response = client.chat.completions(
        messages=[{"role": "system", "content": system_prompt}, *state["messages"]],
        model="sarvam-30b",
    )
    reply = response.choices[0].message.content
    parsed = extract_json(reply)
    if parsed is None:
        logger.warning("scheduler: LLM reply wasn't parseable JSON, treating as clarification: %r", reply)
        return {"action": "clarify", "reply": reply, "distress": False}
    return parsed


def _format_slot_options(slots: list[dict]) -> str:
    lines = [f"- {s['doctor_name']}, {s['department']}, {s['time']}" for s in slots]
    return "\n".join(lines)


async def scheduler_node(state: AgentState) -> AgentState:
    state["current_agent"] = "scheduler"
    logger.info("scheduler: start (call_id=%s, department=%s)", state.get("call_id"), state.get("department"))

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

    script_en = "This is a reminder call about your upcoming appointment tomorrow. Will you be attending?"
    script = await translate_text(script_en, source_lang="en-IN", target_lang=lang_code)
    messages = [*state["messages"], {"role": "assistant", "content": script}]

    decision = await _decide_scheduler_action({**state, "messages": messages})

    if decision.get("action") in ("cancel", "reschedule") or decision.get("distress"):
        logger.info("scheduler_outbound: patient declined/rescheduled, handing off to inbound scheduler flow")
        return await scheduler_node({**state, "messages": messages})

    logger.info("scheduler_outbound: confirming appointment_id=%s", state["appointment_id"])
    await confirm_appointment(state["appointment_id"])
    closing = await translate_text(
        "Thank you, your appointment is confirmed. See you then.", source_lang="en-IN", target_lang=lang_code
    )
    messages.append({"role": "assistant", "content": closing})
    return {**state, "messages": messages, "call_outcome": {"confirmed": True}}
