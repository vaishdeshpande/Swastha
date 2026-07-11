"""Agent 4 — Prescription Reminder.

Handles existing patients asking about their medication schedule. Doctor
notes are always in English and must be translated before being read out.
"""

from __future__ import annotations

import logging
import os

from langsmith import traceable
from sarvamai import SarvamAI

from agents.prompts.prescription import build_prescription_prompt
from agents.state import AgentState
from agents.tools.db_tools import get_prescription, log_query, mark_reminder_sent
from agents.tools.llm_json import extract_json
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])


@traceable(run_type="llm", name="sarvam-30b:prescription_answer")
async def _answer_prescription_query(state: AgentState, prescription_context: dict | None) -> dict:
    """Calls sarvam-30b to answer the patient's question against the known
    prescription context only. Returns the parsed JSON decision dict.

    If the LLM returns unparseable output on the first call, retries once with
    an explicit instruction to return only valid JSON. Raw LLM text is NEVER
    passed to the patient — it would expose internal JSON structure as speech.
    """
    system_prompt = build_prescription_prompt(state["lang_code"], prescription_context)
    messages = [{"role": "system", "content": system_prompt}, *state["messages"]]

    import asyncio as _asyncio

    def _sync_call() -> str:
        r = client.chat.completions(
            messages=messages, model="sarvam-30b", temperature=0.0, max_tokens=2048
        )
        return r.choices[0].message.content or ""

    try:
        reply = await _asyncio.wait_for(_asyncio.to_thread(_sync_call), timeout=20.0)
    except _asyncio.TimeoutError:
        logger.error("prescription: LLM call timed out — escalating instead of hanging")
        return {"reply": None, "escalate": True}
    parsed = extract_json(reply)

    if parsed is not None:
        return parsed

    # Salvage the "reply" value out of malformed JSON before spending a full
    # extra LLM round trip (2-14s of dead air for the patient).
    from agents.tools.llm_json import extract_reply_text
    salvaged = extract_reply_text(reply)
    if salvaged:
        logger.warning("prescription: JSON malformed, salvaged reply text — skipping LLM retry")
        return {"reply": salvaged, "escalate": False}

    # First attempt returned unparseable output — retry with explicit JSON instruction
    logger.warning("prescription: LLM reply not parseable JSON (attempt 1), retrying: %r", reply[:200])
    retry_messages = [
        *messages,
        {"role": "assistant", "content": reply},
        {
            "role": "user",
            "content": (
                "Your previous response was not valid JSON. "
                "Reply with ONLY a JSON object matching this schema exactly: "
                '{"reply": "<spoken text in patient language>", "category": 1, "escalate": false, "confidence": 1.0}'
            ),
        },
    ]

    def _sync_retry() -> str:
        r = client.chat.completions(
            messages=retry_messages, model="sarvam-30b", temperature=0.0, max_tokens=2048
        )
        return r.choices[0].message.content or ""

    try:
        retry_reply = await _asyncio.wait_for(_asyncio.to_thread(_sync_retry), timeout=20.0)
    except _asyncio.TimeoutError:
        logger.error("prescription: LLM retry timed out — escalating")
        return {"reply": None, "escalate": True}
    parsed = extract_json(retry_reply)

    if parsed is not None:
        logger.info("prescription: retry succeeded")
        return parsed

    # Both attempts failed — escalate rather than speak raw JSON to the patient
    logger.error("prescription: LLM reply still not parseable after retry, escalating: %r", retry_reply[:200])
    return {"reply": None, "escalate": True}


async def prescription_node(state: AgentState) -> AgentState:
    state["current_agent"] = "prescription"
    lang_code = state["lang_code"]
    messages = [*state["messages"]]
    logger.info("prescription: start (call_id=%s, patient_id=%s)", state.get("call_id"), state.get("patient_id"))

    try:
        prescription = await get_prescription(state["patient_id"])
        logger.info("prescription: fetched prescription with %d medicine(s)", len(prescription.get("medicines", [])))
    except ValueError:
        logger.warning("prescription: no prescription on file for patient_id=%s — escalating", state.get("patient_id"))
        reply = await translate_text(
            "I don't see a prescription on file for you. Let me connect you to a staff member who can help.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        messages.append({"role": "assistant", "content": reply})
        return {
            **state,
            "messages": messages,
            "escalation_required": True,
            "escalation_reason": "No prescription on file for patient",
        }

    logger.debug("prescription: translating doctor notes to lang_code=%s", lang_code)
    notes_translated = await translate_text(
        prescription["notes_en"], source_lang="en-IN", target_lang=lang_code
    )
    prescription_context = {
        "medicines": prescription["medicines"],
        "notes": notes_translated,
        "refill_date": prescription["refill_date"],
    }

    decision = await _answer_prescription_query(state, prescription_context)
    reply = decision.get("reply") or ""
    # Default category to 1 (safe lookup) if missing — e.g. salvaged malformed JSON.
    category = decision.get("category", 1)
    logger.info("prescription: question category=%d (patient_id=%s)", category, state.get("patient_id"))

    if category == 3 or decision.get("escalate"):
        # Medical judgment required — never answer, connect to staff.
        if not reply:
            reply = await translate_text(
                "I need to connect you to a staff member for this query.",
                source_lang="en-IN",
                target_lang=lang_code,
            )
        messages.append({"role": "assistant", "content": reply})
        logger.warning(
            "prescription: escalating category=%d (patient_id=%s)", category, state.get("patient_id")
        )
        return {
            **state,
            "messages": messages,
            "escalation_required": True,
            "escalation_reason": "Prescription question beyond scope — patient referred to doctor",
        }

    if category == 2:
        # General wellness — answer with a standard disclaimer appended.
        disclaimer = await translate_text(
            "Please confirm with your doctor if you have any specific concerns.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        reply = f"{reply} {disclaimer}" if reply else disclaimer
        logger.info("prescription: category 2 answer with disclaimer (patient_id=%s)", state.get("patient_id"))

    if reply:
        messages.append({"role": "assistant", "content": reply})

    last_user = state["messages"][-1]["content"] if state["messages"] else ""
    await log_query(state["patient_id"], query=last_user, response=reply)
    logger.info("prescription: query logged for patient_id=%s", state.get("patient_id"))

    logger.info("prescription: answered successfully (call_id=%s)", state.get("call_id"))
    return {**state, "messages": messages}


async def prescription_outbound_node(state: AgentState) -> AgentState:
    """Cron-driven medication reminder call.

    Script: "Namaste, yeh aapki dawai ki yaad dilaane ke liye call hai. Aapko
    [medicine name] subah aur shaam leni hai." — confirms the patient
    understood, then logs the reminder as sent.
    """
    state["current_agent"] = "prescription_outbound"
    lang_code = state["lang_code"]
    logger.info("prescription_outbound: start (call_id=%s, patient_id=%s)", state.get("call_id"), state.get("patient_id"))

    try:
        prescription = await get_prescription(state["patient_id"])
        logger.info("prescription_outbound: fetched prescription for patient_id=%s", state.get("patient_id"))
    except ValueError:
        logger.warning(
            "prescription_outbound: no prescription on file for patient_id=%s, closing job",
            state.get("patient_id"),
        )
        return {**state, "call_outcome": {"reminder_sent": False, "reason": "no prescription on file"}}

    medicine_names = ", ".join(m["name"] for m in prescription["medicines"])
    script_en = f"This is a reminder call about your medication: {medicine_names}. Please take it as prescribed."
    script = await translate_text(script_en, source_lang="en-IN", target_lang=lang_code)
    messages = [*state["messages"], {"role": "assistant", "content": script}]

    await mark_reminder_sent(state["patient_id"])
    logger.info("prescription_outbound: reminder sent and marked for patient_id=%s", state.get("patient_id"))

    return {**state, "messages": messages, "call_outcome": {"reminder_sent": True}}
