"""Agent 7 — Billing.

Pure lookup agent. Calls get_bill() once, reads the amount to the patient in
their language, then dispatches a UPI payment link via SMS. No LLM reasoning.

Resilience: every DB/SMS call is wrapped in try/except so a Twilio failure or
missing payment_link does NOT trigger escalation — the patient still hears their
bill amount even if the SMS dispatch fails.

Phone correction: same pattern as lab_status — detects "sorry wrong number"
mid-session and resets patient_id for re-lookup next turn.
"""

from __future__ import annotations

import logging
import re

from agents.state import AgentState
from agents.tools.db_tools import dispatch_payment_link, get_bill, get_patient_record_by_id
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

_CORRECTION_SIGNALS = [
    "galat", "wrong", "nahi", "नहीं", "चुकीचा", "sorry", "mistake",
    "correction", "correct", "actually", "aslat", "dusra", "doosra",
]


def _extract_correction_phone(messages: list[dict], current_phone_digits: str) -> str | None:
    if not messages:
        return None
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
    )
    lower = last_user.lower()
    if not any(sig in lower for sig in _CORRECTION_SIGNALS):
        return None
    candidates = re.findall(r"\b\d{10}\b", last_user)
    for candidate in candidates:
        if candidate != current_phone_digits:
            return candidate
    return None


async def billing_node(state: AgentState) -> AgentState:
    state["current_agent"] = "billing"
    patient_id = state.get("patient_id")
    lang_code = state.get("lang_code", "hi-IN")
    logger.info("billing: start (patient_id=%s, lang_code=%s)", patient_id, lang_code)

    if not patient_id:
        logger.warning("billing: no patient_id in state — cannot look up bill")
        message = await translate_text(
            "I was unable to find your account. Please contact the billing counter.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    # Phone correction check
    collected_phone = (state.get("intake_collected") or {}).get("phone", "")
    current_digits = re.sub(r"\D", "", str(collected_phone))
    corrected_phone = _extract_correction_phone(state["messages"], current_digits)
    if corrected_phone:
        logger.info("billing: phone correction detected — new phone=%s, re-routing via intake", corrected_phone)
        apology = await translate_text(
            "No problem, let me look up your account with the correct number.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        new_collected = {**(state.get("intake_collected") or {}), "phone": corrected_phone}
        return {
            **state,
            "messages": [*state["messages"], {"role": "assistant", "content": apology}],
            "patient_id": None,
            "intent": state.get("intent"),
            "intake_collected": new_collected,
            "bill_amount_due": None,
            "bill_sms_sent": None,
        }

    try:
        bill = await get_bill(patient_id)
    except Exception:
        logger.exception("billing: get_bill failed for patient_id=%s", patient_id)
        message = await translate_text(
            "I'm having trouble accessing your billing information right now. Please try again in a moment.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    logger.debug("billing: get_bill patient_id=%s -> %s", patient_id, bill)

    if not bill:
        message = await translate_text(
            "No outstanding bills found for your account.",
            source_lang="en-IN",
            target_lang=lang_code,
        )
        return {**state, "messages": [*state["messages"], {"role": "assistant", "content": message}]}

    # Build bill summary in English, translate to patient's language
    amount_str = f"₹{bill['amount_due']:,.0f}"
    bill_summary_en = f"Your outstanding bill is {amount_str}."
    items = bill.get("items_json") or []
    if items:
        item_strs = ", ".join([f"{i['desc']} ({i['amount']})" for i in items[:3]])
        bill_summary_en += f" This includes: {item_strs}."

    try:
        translated = await translate_text(bill_summary_en, source_lang="en-IN", target_lang=lang_code)
    except Exception:
        logger.exception("billing: translation failed, using English")
        translated = bill_summary_en

    messages = [*state["messages"], {"role": "assistant", "content": translated}]
    sms_sent = False

    # Dispatch payment link — non-fatal if it fails (patient still heard the amount)
    if bill.get("payment_link"):
        try:
            patient = await get_patient_record_by_id(patient_id)
            if patient and patient.get("phone"):
                await dispatch_payment_link(bill["bill_id"], patient["phone"])
                sms_sent = True
                logger.info("billing: dispatched payment link for bill_id=%s to phone=%s", bill["bill_id"], patient["phone"])
                link_msg = await translate_text(
                    "A payment link has been sent to your registered mobile number.",
                    source_lang="en-IN",
                    target_lang=lang_code,
                )
                messages.append({"role": "assistant", "content": link_msg})
        except Exception:
            logger.exception("billing: payment link dispatch failed for bill_id=%s (non-fatal — patient heard amount)", bill["bill_id"])
            # Do NOT escalate — patient already heard the amount

    return {
        **state,
        "messages": messages,
        "bill_amount_due": bill["amount_due"],
        "bill_sms_sent": sms_sent,
    }
