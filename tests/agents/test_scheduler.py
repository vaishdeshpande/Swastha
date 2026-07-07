"""Tests for Agent 3 — Appointment Scheduler."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_state

OPEN_SLOTS = [
    {"slot_id": "slot-1", "doctor_name": "Dr. Priya Sharma", "department": "general", "date": "2026-07-10", "time": "10:00"},
    {"slot_id": "slot-2", "doctor_name": "Dr. Priya Sharma", "department": "general", "date": "2026-07-10", "time": "11:00"},
]

BOOKING_CONFIRMATION = {
    "appointment_id": "appt-uuid-1",
    "doctor_name": "Dr. Priya Sharma",
    "date": "2026-07-10",
    "time": "10:00",
    "department": "general",
}


def _mock_llm_json(payload: dict):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(payload)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
async def test_check_slots_action_returns_slot_list():
    """action=check_slots → calls check_available_slots and appends reply."""
    state = make_state(intent="book", department="general")
    mock_client = _mock_llm_json({"action": "check_slots", "date": "2026-07-10", "distress": False})

    with (
        patch("agents.agent_scheduler.client", mock_client),
        patch("agents.agent_scheduler.check_available_slots", AsyncMock(return_value=OPEN_SLOTS)),
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    assert result["offered_slots"] is not None
    assert len(result["offered_slots"]) <= 3
    assert result["messages"][-1]["role"] == "assistant"
    assert "Dr. Priya Sharma" in result["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_check_slots_empty_falls_back_to_next_available():
    """If check_available_slots returns [], get_next_available is called."""
    state = make_state(department="cardiology")
    mock_client = _mock_llm_json({"action": "check_slots", "date": "2026-07-10", "distress": False})

    with (
        patch("agents.agent_scheduler.client", mock_client),
        patch("agents.agent_scheduler.check_available_slots", AsyncMock(return_value=[])),
        patch("agents.agent_scheduler.get_next_available", AsyncMock(return_value=OPEN_SLOTS)) as mock_next,
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    mock_next.assert_called_once()
    assert result["offered_slots"] is not None


@pytest.mark.asyncio
async def test_confirm_booking_books_slot_and_sets_appointment_id():
    """action=confirm_booking → calls book_slot and stores appointment_id."""
    state = make_state(department="general", offered_slots=OPEN_SLOTS)
    mock_client = _mock_llm_json({
        "action": "confirm_booking",
        "chosen_slot_id": "slot-1",
        "distress": False,
    })

    with (
        patch("agents.agent_scheduler.client", mock_client),
        patch("agents.agent_scheduler.book_slot", AsyncMock(return_value=BOOKING_CONFIRMATION)),
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    assert result["appointment_id"] == "appt-uuid-1"
    assert result["offered_slots"] is None
    assert "Dr. Priya Sharma" in result["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_cancel_action_cancels_appointment():
    """action=cancel → calls cancel_appointment and clears appointment_id."""
    state = make_state(appointment_id="appt-uuid-1")
    mock_client = _mock_llm_json({
        "action": "cancel",
        "cancel_appointment_id": "appt-uuid-1",
        "distress": False,
    })

    with (
        patch("agents.agent_scheduler.client", mock_client),
        patch("agents.agent_scheduler.cancel_appointment", AsyncMock(return_value=True)),
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    assert result["appointment_id"] is None


@pytest.mark.asyncio
async def test_distress_triggers_escalation():
    """distress=True → escalation_required becomes True without booking."""
    state = make_state()
    mock_client = _mock_llm_json({"action": "clarify", "distress": True})

    with patch("agents.agent_scheduler.client", mock_client):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    assert result["escalation_required"] is True
    assert result["appointment_id"] is None


@pytest.mark.asyncio
async def test_clarify_action_appends_reply():
    """action=clarify → LLM reply is appended to messages, no booking."""
    state = make_state()
    mock_client = _mock_llm_json({"action": "clarify", "reply": "Aap kaunsi date prefer karenge?", "distress": False})

    with patch("agents.agent_scheduler.client", mock_client):
        from agents.agent_scheduler import scheduler_node
        result = await scheduler_node(state)

    assert result["messages"][-1]["content"] == "Aap kaunsi date prefer karenge?"
    assert result["escalation_required"] is False
