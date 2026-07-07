"""Tests for Agent 4 — Prescription."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_state

SAMPLE_PRESCRIPTION = {
    "medicines": [
        {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily morning", "duration": "30 days"},
        {"name": "Aspirin", "dosage": "75mg", "frequency": "once daily after lunch", "duration": "30 days"},
    ],
    "notes_en": "Blood pressure well controlled. Reduce salt intake.",
    "refill_date": "2026-08-05",
}


def _mock_llm_json(payload: dict):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(payload)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
async def test_prescription_fetched_and_answered():
    """Happy path: prescription found, notes translated, answer returned."""
    state = make_state(
        messages=[{"role": "user", "content": "Meri dawai ka schedule kya hai?"}],
    )
    mock_client = _mock_llm_json({"reply": "Aapko Amlodipine subah leni hai", "escalate": False})

    with (
        patch("agents.agent_prescription.client", mock_client),
        patch("agents.agent_prescription.get_prescription", AsyncMock(return_value=SAMPLE_PRESCRIPTION)),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_prescription.log_query", AsyncMock()),
    ):
        from agents.agent_prescription import prescription_node
        result = await prescription_node(state)

    assert result["escalation_required"] is False
    assert result["messages"][-1]["content"] == "Aapko Amlodipine subah leni hai"


@pytest.mark.asyncio
async def test_missing_prescription_escalates():
    """ValueError from get_prescription → escalation, not a crash."""
    state = make_state()

    with (
        patch("agents.agent_prescription.get_prescription", AsyncMock(side_effect=ValueError("no rx"))),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_prescription import prescription_node
        result = await prescription_node(state)

    assert result["escalation_required"] is True
    assert "No prescription" in result["escalation_reason"]


@pytest.mark.asyncio
async def test_out_of_scope_question_escalates():
    """LLM returning escalate=True triggers human handoff."""
    state = make_state(
        messages=[{"role": "user", "content": "Kya main ye dawai band kar sakta hoon?"}],
    )
    mock_client = _mock_llm_json({"reply": "Iske liye doctor se milein", "escalate": True})

    with (
        patch("agents.agent_prescription.client", mock_client),
        patch("agents.agent_prescription.get_prescription", AsyncMock(return_value=SAMPLE_PRESCRIPTION)),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_prescription.log_query", AsyncMock()),
    ):
        from agents.agent_prescription import prescription_node
        result = await prescription_node(state)

    assert result["escalation_required"] is True
    assert result["messages"][-1]["content"] == "Iske liye doctor se milein"


@pytest.mark.asyncio
async def test_outbound_reminder_sent():
    """Outbound reminder: medicine names spoken and reminder marked sent."""
    state = make_state(job_type="rx_reminder")

    with (
        patch("agents.agent_prescription.get_prescription", AsyncMock(return_value=SAMPLE_PRESCRIPTION)),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_prescription.mark_reminder_sent", AsyncMock()) as mock_mark,
    ):
        from agents.agent_prescription import prescription_outbound_node
        result = await prescription_outbound_node(state)

    mock_mark.assert_called_once_with(state["patient_id"])
    assert result["call_outcome"]["reminder_sent"] is True
    assert "Amlodipine" in result["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_outbound_missing_prescription_closes_gracefully():
    """Outbound job with no prescription returns reminder_sent=False, no crash."""
    state = make_state(job_type="rx_reminder")

    with patch("agents.agent_prescription.get_prescription", AsyncMock(side_effect=ValueError("no rx"))):
        from agents.agent_prescription import prescription_outbound_node
        result = await prescription_outbound_node(state)

    assert result["call_outcome"]["reminder_sent"] is False
