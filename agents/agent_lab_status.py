"""Agent 6 — Lab Status.

Pure lookup agent. Calls get_lab_status() once, translates results, appends
messages. No LLM decision — no Tool-Calling/ReAct pattern here.

Phone correction: if the patient says a different 10-digit number mid-session
(e.g. "sorry, wrong number — it's 9876543210"), we detect it here and reset
patient_id so the next turn re-routes through voice_intake with the new phone.
"""

import logging
import re

from agents.state import AgentState
from agents.tools.db_tools import get_lab_status, get_patient_record, mark_report_dispatched
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

_CORRECTION_SIGNALS = [
    "galat", "wrong", "nahi", "नहीं", "चुकीचा", "sorry", "mistake",
    "correction", "correct", "actually", "aslat", "dusra", "doosra",
]


def _extract_correction_phone(messages: list[dict], current_phone_digits: str) -> str | None:
    """Scan the last user message for a 10-digit number that differs from the
    currently registered phone. Returns the new number if a correction is detected."""
    if not messages:
        return None
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    lower = last_user.lower()

    # Only act if the message contains a correction signal word
    if not any(sig in lower for sig in _CORRECTION_SIGNALS):
        return None

    # Extract all 10-digit sequences from the message
    candidates = re.findall(r"\b\d{10}\b", last_user)
    for candidate in candidates:
        if candidate != current_phone_digits:
            return candidate
    return None


async def lab_status_node(state: AgentState) -> AgentState:
    state["current_agent"] = "lab_status"
    patient_id = state.get("patient_id")
    lang_code = state.get("lang_code", "hi-IN")
    logger.info("lab_status: start (patient_id=%s, lang_code=%s)", patient_id, lang_code)

    if not patient_id:
        logger.warning("lab_status: no patient_id in state — cannot look up reports")
        message = await translate_text(
            "I was unable to find your records. Please contact the lab counter.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    # Phone correction check — patient may say "sorry wrong number, it's XXXXXXXXXX"
    collected_phone = (state.get("intake_collected") or {}).get("phone", "")
    current_digits = re.sub(r"\D", "", str(collected_phone))
    corrected_phone = _extract_correction_phone(state["messages"], current_digits)
    if corrected_phone:
        logger.info("lab_status: phone correction detected — new phone=%s, re-routing via intake", corrected_phone)
        # Reset patient identity so voice_intake re-runs the lookup next turn
        apology = await translate_text(
            "No problem, let me look up your records with the correct number.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        new_collected = {**(state.get("intake_collected") or {}), "phone": corrected_phone}
        return {
            **state,
            "messages": [*state["messages"], {"role": "assistant", "content": apology}],
            "patient_id": None,
            "intent": state.get("intent"),   # keep lab intent so re-routing works
            "intake_collected": new_collected,
            "lab_reports_dispatched": None,
        }

    try:
        reports = await get_lab_status(patient_id)
    except Exception:
        logger.exception("lab_status: get_lab_status failed for patient_id=%s", patient_id)
        message = await translate_text(
            "I'm having trouble accessing your lab reports right now. Please try again in a moment.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    logger.debug("lab_status: fetched %d report(s) for patient_id=%s", len(reports), patient_id)

    if not reports:
        message = await translate_text(
            "No lab reports are currently on file for you. Please contact the lab counter.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    ready = [r for r in reports if r["status"] == "ready"]
    pending = [r for r in reports if r["status"] == "pending"]

    messages = [*state["messages"]]
    dispatched_reports: list[dict] = []

    if ready:
        for report in ready:
            try:
                translated_summary = await translate_text(
                    report["result_summary_en"],
                    source_lang="en-IN",
                    target_lang=lang_code,
                )
            except Exception:
                logger.exception("lab_status: translation failed for report_id=%s", report["report_id"])
                translated_summary = report["result_summary_en"]  # fall back to English
            message = f"{report['test_name']}: {translated_summary}"
            messages.append({"role": "assistant", "content": message})
            dispatched_reports.append({"test_name": report["test_name"], "summary": translated_summary})
            try:
                await mark_report_dispatched(report["report_id"])
                logger.info("lab_status: dispatched report_id=%s test=%s", report["report_id"], report["test_name"])
            except Exception:
                logger.exception("lab_status: mark_report_dispatched failed for report_id=%s (non-fatal)", report["report_id"])

    if pending:
        test_names = ", ".join([r["test_name"] for r in pending])
        try:
            pending_msg = await translate_text(
                f"The following tests are still being processed: {test_names}. Please check back later.",
                source_lang="en-IN",
                target_lang=lang_code,
            )
        except Exception:
            pending_msg = f"The following tests are still being processed: {test_names}. Please check back later."
        messages.append({"role": "assistant", "content": pending_msg})
        for r in pending:
            dispatched_reports.append({"test_name": r["test_name"], "summary": None})

    return {**state, "messages": messages, "lab_reports_dispatched": dispatched_reports or None}
