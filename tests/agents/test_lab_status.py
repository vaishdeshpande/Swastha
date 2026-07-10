"""Tests for Agent 6 — Lab Status."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import make_state

READY_REPORT = {
    "report_id": "report-cbc-uuid",
    "test_name": "Complete Blood Count (CBC)",
    "status": "ready",
    "ready_at": "2026-07-06T14:00:00",
    "result_summary_en": "Hemoglobin is slightly low at 10.8 g/dL.",
}

PENDING_REPORT = {
    "report_id": "report-lipid-uuid",
    "test_name": "Lipid Panel",
    "status": "pending",
    "ready_at": None,
    "result_summary_en": None,
}


@pytest.mark.asyncio
async def test_ready_report_read_and_dispatched():
    """Happy path: one ready report → translated summary appended, mark_report_dispatched called."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[READY_REPORT])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()) as mock_dispatch,
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    assert result["current_agent"] == "lab_status"
    assert result["escalation_required"] is False
    last_msg = result["messages"][-1]["content"]
    assert "Complete Blood Count (CBC)" in last_msg
    assert "10.8 g/dL" in last_msg
    mock_dispatch.assert_called_once_with("report-cbc-uuid")


@pytest.mark.asyncio
async def test_ready_report_sets_lab_reports_dispatched():
    """lab_reports_dispatched state field is populated with the dispatched report data."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[READY_REPORT])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()),
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    dispatched = result["lab_reports_dispatched"]
    assert dispatched is not None
    assert len(dispatched) == 1
    assert dispatched[0]["test_name"] == "Complete Blood Count (CBC)"
    assert dispatched[0]["summary"] is not None


@pytest.mark.asyncio
async def test_pending_report_shows_processing_message():
    """Pending report → 'still being processed' message, mark_report_dispatched NOT called."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[PENDING_REPORT])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()) as mock_dispatch,
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    last_msg = result["messages"][-1]["content"]
    assert "Lipid Panel" in last_msg
    assert "processing" in last_msg.lower() or "processed" in last_msg.lower()
    mock_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_no_reports_returns_apology_no_escalation():
    """No reports on file → apologetic message, escalation_required stays False."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()),
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    assert result["escalation_required"] is False
    last_msg = result["messages"][-1]["content"]
    assert "no lab reports" in last_msg.lower() or "lab counter" in last_msg.lower()


@pytest.mark.asyncio
async def test_mixed_ready_and_pending_reports():
    """Both ready and pending reports present → ready one is read, pending listed as processing."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[READY_REPORT, PENDING_REPORT])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()) as mock_dispatch,
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    # Two messages: one for ready, one for pending
    assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 2
    contents = " ".join(m["content"] for m in assistant_msgs)
    assert "Complete Blood Count" in contents
    assert "Lipid Panel" in contents
    # Only the ready report is dispatched
    mock_dispatch.assert_called_once_with("report-cbc-uuid")


@pytest.mark.asyncio
async def test_missing_patient_id_returns_error_message():
    """No patient_id in state → returns error message without calling get_lab_status."""
    state = make_state(patient_id=None, messages=[])

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock()) as mock_get,
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()),
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    mock_get.assert_not_called()
    last_msg = result["messages"][-1]["content"]
    assert "unable" in last_msg.lower() or "contact" in last_msg.lower()


@pytest.mark.asyncio
async def test_dispatched_reports_not_returned_by_get_lab_status():
    """get_lab_status is assumed to filter dispatched rows — verify node handles empty correctly."""
    state = make_state(messages=[])

    # Simulate: only dispatched rows existed — get_lab_status returns empty (filters them)
    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()) as mock_dispatch,
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_lab_status import lab_status_node
        result = await lab_status_node(state)

    # Should NOT re-dispatch anything
    mock_dispatch.assert_not_called()
    assert result["escalation_required"] is False


@pytest.mark.asyncio
async def test_marathi_patient_translate_called_with_correct_lang():
    """translate_text is called with target_lang=mr-IN for Marathi patient."""
    state = make_state(lang_code="mr-IN", tts_voice="kavya", messages=[])
    translate_calls: list[tuple] = []

    async def capture_translate(text, source_lang="en-IN", target_lang="hi-IN"):
        translate_calls.append((text, target_lang))
        return text

    with (
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=[READY_REPORT])),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()),
        patch("agents.agent_lab_status.translate_text", capture_translate),
    ):
        from agents.agent_lab_status import lab_status_node
        await lab_status_node(state)

    assert any(lang == "mr-IN" for _, lang in translate_calls)
