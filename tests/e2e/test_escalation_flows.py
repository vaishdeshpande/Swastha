"""
E2E Scenario 3 — Escalation Flows
====================================

Every path that ends at human_handoff_node.

KEY: When voice_intake loops 3x before escalating, all 3 LLM responses
must be in one run_turn() call — the loops happen within the same ainvoke().
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    PRESCRIPTION_RAMESH,
    intake_book,
    intake_unclear,
    intake_prescription,
    rx_escalate,
)


@pytest.mark.asyncio
async def test_three_unclear_loops_in_one_turn_trigger_escalation():
    """
    Patient sends one vague message. Graph loops voice_intake 3 times
    within the SINGLE ainvoke() before escalating.

    3 LLM calls happen inside one run_turn():
      [unclear, unclear, unclear] → escalation → human_handoff → END

    Patient hears ONE final message (the transfer message from human_handoff).
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            intake_unclear("Main aapki baat samajh nahi paaya"),  # loop 1, count=1
            intake_unclear("Kripya dobara batayein"),             # loop 2, count=2
            intake_unclear("Kya aap appointment chahte hain?"),   # loop 3, count=3 → escalate
        ],
        patient=PATIENT_RAMESH,
    ):
        reply, state = await run_turn(inbound_graph, state, "Haan woh cheez chahiye")

    print_state("TURN — 3 unclear loops → escalation in single ainvoke()", state)

    assert state["escalation_required"] is True
    assert state["intake_attempt_count"] == 3
    # Transfer message from human_handoff_node
    assert reply    # patient hears something, not silence
    assert state["messages"][-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_escalation_from_prescription_out_of_scope():
    """
    Agent 4 returns escalate=True → human_handoff_node fires →
    escalate_to_doctor called with patient_id and reason.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    escalate_doctor_mock = AsyncMock()

    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_escalate("Is sawaal ke liye doctor se baat karein."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        with patch("agents.graph.escalate_to_doctor", escalate_doctor_mock):
            reply, state = await run_turn(
                inbound_graph, state,
                "Kya main dawai bina doctor ke band kar sakta hoon? Ramesh, +919876543210",
            )

    print_state("TURN — prescription escalation", state)

    assert state["escalation_required"] is True
    escalate_doctor_mock.assert_called_once()
    call_args = escalate_doctor_mock.call_args
    assert call_args.args[0] == PATIENT_RAMESH["id"]
    assert call_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_escalation_from_scheduler_distress():
    """
    Scheduler LLM returns distress=True → scheduler_node sets
    escalation_required=True without booking anything.
    """
    state = fresh_state(
        detected_language="hi-IN",
        detection_confidence=0.9,
        intent="book",
        department="general",
        patient_id=PATIENT_RAMESH["id"],
        messages=[{"role": "user", "content": "Mujhe confusion ho rahi hai"}],
    )

    with graph_mocks(
        llm_responses=[
            intake_book(),
            {"action": "clarify", "reply": "Kya main madad kar sakta hoon?", "distress": True},
        ],
        patient=PATIENT_RAMESH,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Bahut confusion hai kuch samajh nahi aa raha",
        )

    print_state("TURN — scheduler distress escalation", state)

    assert state["escalation_required"] is True
    assert state["appointment_id"] is None


@pytest.mark.asyncio
async def test_human_handoff_preserves_prior_call_outcome():
    """
    If call_outcome had partial data before escalation, human_handoff_node
    must merge {status: escalated} without wiping the existing fields.
    """
    state = fresh_state(
        detected_language="hi-IN",
        detection_confidence=0.9,
        intent="book",
        department="general",
        patient_id=PATIENT_RAMESH["id"],
        call_outcome={"partial_booking": True, "notes": "patient called twice"},
        messages=[{"role": "user", "content": "Help chahiye"}],
    )

    with graph_mocks(
        llm_responses=[
            intake_book(),
            {"action": "clarify", "distress": True},
        ],
        patient=PATIENT_RAMESH,
    ):
        _, state = await run_turn(inbound_graph, state, "Kuch samajh nahi aa raha")

    print_state("TURN — prior call_outcome preserved through escalation", state)

    assert state["call_outcome"]["status"] == "escalated"
    assert state["call_outcome"]["partial_booking"] is True   # not wiped
    assert state["call_outcome"]["notes"] == "patient called twice"


@pytest.mark.asyncio
async def test_escalation_reply_is_non_empty():
    """
    Patient must always hear something when escalated — not silence.
    human_handoff_node appends the transfer message to messages.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            intake_unclear("Samajh nahi aaya"),
            intake_unclear("Dobara batayein"),
            intake_unclear("Kya madad chahiye?"),
        ],
        patient=PATIENT_RAMESH,
    ):
        reply, state = await run_turn(inbound_graph, state, "Bas kuch chahiye tha")

    # Reply is the transfer message from human_handoff_node
    assert reply
    assert len(reply) > 0
    assert state["escalation_required"] is True
