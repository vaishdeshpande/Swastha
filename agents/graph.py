"""LangGraph StateGraph definitions — inbound (per-call) and outbound (cron) graphs."""

import logging

from langgraph.graph import StateGraph, END

from agents.state import AgentState
from agents.agent_language_router import language_router_node
from agents.agent_voice_intake import voice_intake_node
from agents.agent_scheduler import scheduler_node, scheduler_outbound_node
from agents.agent_prescription import prescription_node, prescription_outbound_node
from agents.agent_followup import followup_outbound_node
from agents.agent_lab_status import lab_status_node
from agents.agent_billing import billing_node
from agents.tools.notification_tools import escalate_to_doctor
from agents.tools.translate_tools import translate_text
from analytics.call_analytics import post_call_node

logger = logging.getLogger(__name__)

MAX_INTAKE_ATTEMPTS = 3


async def human_handoff_node(state: AgentState) -> AgentState:
    """A patient-facing agent set escalation_required=True. Tell the patient
    a staff member will take over, and alert the on-call doctor with the
    reason so they can pick up the call."""
    state["current_agent"] = "human_handoff"
    reason = state.get("escalation_reason") or "Escalated to human handoff"
    logger.warning(
        "human_handoff: escalating call (call_id=%s, patient_id=%s, reason=%s)",
        state.get("call_id"), state.get("patient_id"), reason,
    )

    reply = await translate_text(
        "I'm connecting you to one of our staff members who can help you further.",
        source_lang="en-IN",
        target_lang=state["lang_code"],
    )
    messages = [*state["messages"], {"role": "assistant", "content": reply}]

    if state.get("patient_id"):
        await escalate_to_doctor(state["patient_id"], reason=reason)
        logger.info("human_handoff: doctor notified for patient_id=%s", state.get("patient_id"))
    else:
        logger.warning("human_handoff: skipping doctor notification — patient_id unknown")

    return {
        **state,
        "messages": messages,
        "call_outcome": {**(state.get("call_outcome") or {}), "status": "escalated"},
    }


async def escalate_node(state: AgentState) -> AgentState:
    """Agent 5 (follow-up) flagged a high readmission risk. Alert the
    on-call doctor — the actual escalate_to_doctor call already happened
    inside agent_followup.py's own workflow; this node just marks the
    outbound job as escalated for the cron/analytics layer."""
    state["current_agent"] = "escalate"
    outcome = state.get("call_outcome") or {}
    logger.info(
        "escalate: marking outbound job escalated (patient_id=%s, risk=%.2f)",
        state.get("patient_id"), outcome.get("readmission_risk", 0.0),
    )
    return {**state, "call_outcome": {**outcome, "status": "escalated"}}


async def route_outbound_job_node(state: AgentState) -> AgentState:
    state["current_agent"] = "route_job"
    logger.info(
        "route_job: routing outbound job (patient_id=%s, job_type=%s)",
        state.get("patient_id"), state.get("job_type"),
    )
    return state


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def route_after_language(state: AgentState) -> str:
    """Skip voice_intake on turns where intake is already resolved.

    Once intent + patient_id are both known, subsequent turns (e.g. patient
    choosing a slot, asking a follow-up question) go directly to the specialist
    node. This eliminates the redundant sarvam-30b extraction call that would
    otherwise run on every turn — ~1.9s TTFT saved per mid-conversation turn.
    """
    if state.get("escalation_required", False):
        return "voice_intake"  # let intake path handle escalation display

    intent = state.get("intent")
    patient_id = state.get("patient_id")

    specialist_map = {
        "book": "scheduler",
        "prescription": "prescription",
        "lab": "lab_status",
        "billing": "billing",
    }

    if intent and patient_id and intent in specialist_map:
        route = specialist_map[intent]
        logger.debug("route_after_language: intake resolved, skipping voice_intake -> %s", route)
        return route

    logger.debug("route_after_language: intake incomplete -> voice_intake")
    return "voice_intake"


def route_after_intake(state: AgentState) -> str:
    """Decides which agent handles the patient's intent.

    Intent is now promoted to state early (even before phone is collected) so
    Scenario 2 slot pre-fetch can fire. We still gate specialist routing on
    patient_id — if intent is known but phone hasn't been provided yet,
    return await_input so the next utterance can supply the phone.
    """
    if state.get("escalation_required", False):
        logger.debug("route_after_intake: escalation_required -> human_handoff")
        return "human_handoff"

    intent = state.get("intent")

    # Phone not yet collected — intent is known but we can't call the DB.
    # Voice intake's LLM reply already asked for phone, so wait for next turn.
    if intent and not state.get("patient_id"):
        logger.debug("route_after_intake: intent=%s but no patient_id yet -> await_input", intent)
        return "await_input"

    intent_routes = {
        "book": "scheduler",
        "prescription": "prescription",
        "lab": "lab_status",
        "billing": "billing",
        # "followup" is outbound-only (Agent 5). If a patient says "follow up" inbound,
        # keep them in the intake loop to clarify what they actually need.
        # "query" is too generic — clarify before routing.
    }

    if intent in intent_routes:
        route = intent_routes[intent]
        logger.debug("route_after_intake: intent=%s -> %s", intent, route)
        return route

    # Intent still unclear — check if we've exhausted attempts
    attempt = state.get("intake_attempt_count", 0)
    if attempt >= MAX_INTAKE_ATTEMPTS:
        logger.warning("route_after_intake: max attempts reached without intent -> human_handoff")
        return "human_handoff"

    logger.debug("route_after_intake: intent unclear -> await_input (attempt=%d)", attempt)
    return "await_input"


def check_escalation(state: AgentState) -> str:
    """After Agent 3 or 4 completes, check if escalation is needed."""
    if state.get("escalation_required", False):
        logger.info("check_escalation: escalation_required=True -> human_handoff")
        return "human_handoff"
    logger.debug("check_escalation: no escalation -> post_call")
    return "post_call"


def route_outbound_job(state: AgentState) -> str:
    """Reads the job_type field set by the cron trigger."""
    job_type = state.get("job_type")
    if job_type == "confirmation":
        return "confirmation"
    elif job_type == "rx_reminder":
        return "rx_reminder"
    elif job_type == "followup":
        return "followup"
    raise ValueError(f"Unknown job_type: {job_type}")


def check_risk(state: AgentState) -> str:
    """After Agent 5 follow-up, check readmission risk."""
    outcome = state.get("call_outcome") or {}
    risk_score = outcome.get("readmission_risk", 0.0)
    if risk_score > 0.7:
        logger.info("check_risk: risk=%.2f > 0.7 -> escalate", risk_score)
        return "escalate"
    logger.debug("check_risk: risk=%.2f -> end", risk_score)
    return "end"


# ---------------------------------------------------------------------------
# Inbound graph — one pass per patient utterance
# ---------------------------------------------------------------------------

def build_inbound_graph():
    graph = StateGraph(AgentState)

    graph.add_node("language_router", language_router_node)
    graph.add_node("voice_intake", voice_intake_node)
    graph.add_node("scheduler", scheduler_node)
    graph.add_node("prescription", prescription_node)
    graph.add_node("lab_status", lab_status_node)
    graph.add_node("billing", billing_node)
    graph.add_node("human_handoff", human_handoff_node)
    graph.add_node("post_call", post_call_node)

    graph.set_entry_point("language_router")

    # After language detection, skip voice_intake when intake is already resolved —
    # saves ~1.9s per mid-conversation turn (e.g. patient choosing a slot).
    graph.add_conditional_edges(
        "language_router",
        route_after_language,
        {
            "voice_intake": "voice_intake",
            "scheduler": "scheduler",
            "prescription": "prescription",
            "lab_status": "lab_status",
            "billing": "billing",
        },
    )

    graph.add_conditional_edges(
        "voice_intake",
        route_after_intake,
        {
            "scheduler": "scheduler",
            "prescription": "prescription",
            "lab_status": "lab_status",
            "billing": "billing",
            "await_input": END,   # stop here; next user utterance restarts the graph
            "human_handoff": "human_handoff",
        },
    )

    graph.add_conditional_edges(
        "scheduler",
        check_escalation,
        {
            "human_handoff": "human_handoff",
            "post_call": "post_call",
        },
    )

    graph.add_conditional_edges(
        "prescription",
        check_escalation,
        {
            "human_handoff": "human_handoff",
            "post_call": "post_call",
        },
    )

    graph.add_conditional_edges(
        "lab_status",
        check_escalation,
        {
            "human_handoff": "human_handoff",
            "post_call": "post_call",
        },
    )

    graph.add_conditional_edges(
        "billing",
        check_escalation,
        {
            "human_handoff": "human_handoff",
            "post_call": "post_call",
        },
    )

    graph.add_edge("human_handoff", END)
    graph.add_edge("post_call", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Outbound graph — triggered by APScheduler cron every 30 minutes
# ---------------------------------------------------------------------------

def build_outbound_graph():
    outbound_graph = StateGraph(AgentState)

    outbound_graph.add_node("route_job", route_outbound_job_node)
    outbound_graph.add_node("scheduler_outbound", scheduler_outbound_node)
    outbound_graph.add_node("prescription_outbound", prescription_outbound_node)
    outbound_graph.add_node("followup_outbound", followup_outbound_node)
    outbound_graph.add_node("escalate", escalate_node)

    outbound_graph.set_entry_point("route_job")

    outbound_graph.add_conditional_edges(
        "route_job",
        route_outbound_job,
        {
            "confirmation": "scheduler_outbound",
            "rx_reminder": "prescription_outbound",
            "followup": "followup_outbound",
        },
    )

    outbound_graph.add_conditional_edges(
        "followup_outbound",
        check_risk,
        {
            "escalate": "escalate",
            "end": END,
        },
    )

    outbound_graph.add_edge("scheduler_outbound", END)
    outbound_graph.add_edge("prescription_outbound", END)
    outbound_graph.add_edge("escalate", END)

    return outbound_graph.compile()


inbound_graph = build_inbound_graph()
outbound_graph = build_outbound_graph()
