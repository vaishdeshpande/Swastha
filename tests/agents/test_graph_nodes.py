"""Tests for non-agent graph nodes: human_handoff_node, escalate_node,
route_outbound_job_node."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import make_state


@pytest.mark.asyncio
async def test_human_handoff_translates_and_notifies():
    """human_handoff_node: translates reply and calls escalate_to_doctor."""
    state = make_state(
        lang_code="hi-IN",
        escalation_reason="Patient confused after 3 rounds",
    )

    with (
        patch("agents.graph.translate_text", AsyncMock(return_value="Aapko staff se connect kar raha hoon")),
        patch("agents.graph.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        from agents.graph import human_handoff_node
        result = await human_handoff_node(state)

    mock_escalate.assert_called_once_with(
        state["patient_id"],
        reason="Patient confused after 3 rounds",
    )
    assert result["call_outcome"]["status"] == "escalated"
    assert result["messages"][-1]["content"] == "Aapko staff se connect kar raha hoon"
    assert result["current_agent"] == "human_handoff"


@pytest.mark.asyncio
async def test_human_handoff_default_reason():
    """human_handoff_node uses a default reason when escalation_reason is None."""
    state = make_state(escalation_reason=None)

    with (
        patch("agents.graph.translate_text", AsyncMock(return_value="Connecting...")),
        patch("agents.graph.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        from agents.graph import human_handoff_node
        await human_handoff_node(state)

    _, kwargs = mock_escalate.call_args
    assert kwargs["reason"] == "Escalated to human handoff"


@pytest.mark.asyncio
async def test_escalate_node_marks_status():
    """escalate_node: merges 'escalated' status into existing call_outcome."""
    state = make_state(call_outcome={"readmission_risk": 0.8, "fever": True})

    from agents.graph import escalate_node
    result = await escalate_node(state)

    assert result["call_outcome"]["status"] == "escalated"
    assert result["call_outcome"]["readmission_risk"] == 0.8
    assert result["current_agent"] == "escalate"


@pytest.mark.asyncio
async def test_escalate_node_no_prior_outcome():
    """escalate_node: works even when call_outcome is None."""
    state = make_state(call_outcome=None)

    from agents.graph import escalate_node
    result = await escalate_node(state)

    assert result["call_outcome"]["status"] == "escalated"


@pytest.mark.asyncio
async def test_route_outbound_job_node_sets_current_agent():
    """route_outbound_job_node: sets current_agent and passes state through."""
    state = make_state(job_type="followup")

    from agents.graph import route_outbound_job_node
    result = await route_outbound_job_node(state)

    assert result["current_agent"] == "route_job"
    assert result["job_type"] == "followup"
