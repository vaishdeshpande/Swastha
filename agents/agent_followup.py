"""Agent 5 — Post-Discharge Follow-up.

Outbound only. Calls discharged patients at 24h/72h to check recovery
status, computes a readmission risk score, and escalates high-risk cases
to the on-call doctor.
"""

import logging
import os
from typing import Optional, TypedDict

from langsmith import traceable
from sarvamai import SarvamAI

from agents.prompts.followup import build_followup_prompt
from agents.state import AgentState
from agents.tools.db_tools import get_discharge_info, log_outcome
from agents.tools.llm_json import extract_json
from agents.tools.notification_tools import escalate_to_doctor
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

RISK_ESCALATION_THRESHOLD = 0.7


class FollowupOutcome(TypedDict):
    fever: bool
    pain_level: int               # 1-10
    medication_adherence: str     # "yes" | "no" | "partial"
    additional_concerns: str
    readmission_risk: float       # 0.0-1.0
    status: str                   # "completed" | "unreachable" | "escalated"


def _compute_readmission_risk(fever: bool, pain_level: int, medication_adherence: str) -> float:
    if fever or pain_level > 7 or medication_adherence == "no":
        return 0.8
    if 4 <= pain_level <= 7 and medication_adherence == "partial":
        return 0.5
    return 0.2


@traceable(run_type="llm", name="sarvam-30b:followup_checklist")
async def _run_checklist_turn(state: AgentState, discharge: dict, collected: dict | None = None) -> dict:
    """Calls sarvam-30b to ask the next unanswered checklist question, or
    report that all four items have been collected.

    `collected` is the code-side memory of fields already answered in previous
    turns. Injecting it explicitly prevents the model from re-asking a question
    it failed to notice was answered (deterministic loop at temperature=0)."""
    system_prompt = build_followup_prompt(
        lang_code=state["lang_code"],
        diagnosis=discharge["diagnosis"],
        medications=discharge["medications"],
    )
    if collected:
        known = ", ".join(f"{k}={v!r}" for k, v in collected.items())
        system_prompt += (
            f"\n\n━━━ ALREADY COLLECTED — DO NOT RE-ASK ━━━\n"
            f"These fields were already answered in earlier turns: {known}\n"
            f"Include these values in your JSON output. Only ask about fields NOT listed here. "
            f"If all four fields are now known, set all_answered=true and close the call."
        )
    import asyncio

    def _sync_call() -> str:
        response = client.chat.completions(
            messages=[{"role": "system", "content": system_prompt}, *state["messages"]],
            model="sarvam-30b",
            temperature=0.0,
            max_tokens=2048,
        )
        return response.choices[0].message.content or ""

    try:
        reply = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=25.0)
    except asyncio.TimeoutError:
        logger.error("followup: checklist LLM call timed out")
        return {"reply": "", "all_answered": False}

    parsed = extract_json(reply)
    if parsed is None:
        logger.warning("followup: LLM reply wasn't parseable JSON, treating as mid-checklist: %r", reply)
        return {"reply": reply, "all_answered": False}
    return parsed


async def followup_outbound_node(state: AgentState) -> AgentState:
    state["current_agent"] = "followup_outbound"
    lang_code = state["lang_code"]
    patient_id = state["patient_id"]
    logger.info("followup_outbound: start (call_id=%s, patient_id=%s)", state.get("call_id"), patient_id)

    # The patient never picked up or it went to voicemail — log and stop.
    # Retrying in 4 hours is scheduled by the caller (get_due_outbound_jobs),
    # not this node.
    if state.get("call_connected") is False:
        logger.warning("followup_outbound: patient unreachable (call_id=%s, patient_id=%s)", state.get("call_id"), patient_id)
        outcome: FollowupOutcome = {
            "fever": False,
            "pain_level": 0,
            "medication_adherence": "",
            "additional_concerns": "",
            "readmission_risk": 0.0,
            "status": "unreachable",
        }
        await log_outcome(patient_id, outcome)
        return {**state, "call_outcome": outcome}

    try:
        discharge = await get_discharge_info(patient_id)
        logger.info("followup_outbound: discharge info fetched (diagnosis=%s)", discharge.get("diagnosis"))
    except ValueError:
        logger.warning("followup_outbound: no discharge record for patient_id=%s, closing job", patient_id)
        outcome: FollowupOutcome = {
            "fever": False,
            "pain_level": 0,
            "medication_adherence": "",
            "additional_concerns": "",
            "readmission_risk": 0.0,
            "status": "unreachable",
        }
        return {**state, "call_outcome": outcome}

    messages = [*state["messages"]]

    # First turn: greet before running the checklist.
    if not messages:
        greeting_en = "Hello, this is a follow-up call from the hospital to check on your recovery."
        greeting = await translate_text(greeting_en, source_lang="en-IN", target_lang=lang_code)
        messages.append({"role": "assistant", "content": greeting})
        logger.info("followup_outbound: sent greeting (patient_id=%s)", patient_id)
        return {**state, "messages": messages}

    checklist = await _run_checklist_turn(state, discharge)
    logger.debug("followup_outbound: checklist turn result — all_answered=%s", checklist.get("all_answered"))

    if not checklist.get("all_answered"):
        if checklist.get("reply"):
            messages.append({"role": "assistant", "content": checklist["reply"]})
        return {**state, "messages": messages}

    if checklist.get("reply"):
        messages.append({"role": "assistant", "content": checklist["reply"]})

    fever = bool(checklist.get("fever"))
    pain_level = int(checklist.get("pain_level") or 0)
    medication_adherence = checklist.get("medication_adherence") or "yes"
    additional_concerns = checklist.get("additional_concerns") or ""

    readmission_risk = _compute_readmission_risk(fever, pain_level, medication_adherence)
    should_escalate = readmission_risk > RISK_ESCALATION_THRESHOLD

    logger.info(
        "followup_outbound: checklist complete — fever=%s pain=%d adherence=%s risk=%.2f escalate=%s",
        fever, pain_level, medication_adherence, readmission_risk, should_escalate,
    )

    outcome: FollowupOutcome = {
        "fever": fever,
        "pain_level": pain_level,
        "medication_adherence": medication_adherence,
        "additional_concerns": additional_concerns,
        "readmission_risk": readmission_risk,
        "status": "escalated" if should_escalate else "completed",
    }
    await log_outcome(patient_id, outcome)
    logger.info("followup_outbound: outcome logged (patient_id=%s, status=%s)", patient_id, outcome["status"])

    if should_escalate:
        logger.warning(
            "followup_outbound: escalating to doctor (patient_id=%s, risk=%.2f)", patient_id, readmission_risk
        )
        await escalate_to_doctor(
            patient_id,
            reason=f"Post-discharge follow-up flagged readmission_risk={readmission_risk}",
        )

    return {**state, "messages": messages, "call_outcome": outcome}
