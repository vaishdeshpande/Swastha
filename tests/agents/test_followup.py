"""Tests for Agent 5 — Post-Discharge Follow-up."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_state
from agents.agent_followup import _compute_readmission_risk

DISCHARGE_INFO = {
    "discharge_date": "2026-07-04T00:00:00",
    "diagnosis": "Appendectomy - laparoscopic",
    "medications": [{"name": "Cefixime", "dosage": "200mg", "frequency": "twice daily"}],
}


# ---------------------------------------------------------------------------
# Pure logic — _compute_readmission_risk
# ---------------------------------------------------------------------------

class TestComputeReadmissionRisk:
    def test_fever_triggers_high_risk(self):
        assert _compute_readmission_risk(fever=True, pain_level=2, medication_adherence="yes") == 0.8

    def test_high_pain_triggers_high_risk(self):
        assert _compute_readmission_risk(fever=False, pain_level=8, medication_adherence="yes") == 0.8

    def test_no_medication_triggers_high_risk(self):
        assert _compute_readmission_risk(fever=False, pain_level=3, medication_adherence="no") == 0.8

    def test_moderate_pain_partial_adherence_mid_risk(self):
        assert _compute_readmission_risk(fever=False, pain_level=5, medication_adherence="partial") == 0.5

    def test_all_normal_low_risk(self):
        assert _compute_readmission_risk(fever=False, pain_level=2, medication_adherence="yes") == 0.2

    def test_boundary_pain_7_adherent_is_low_risk(self):
        # pain_level 7 with full adherence and no fever falls through to the 0.2 branch
        # (mid-risk requires partial adherence AND pain 4–7)
        assert _compute_readmission_risk(fever=False, pain_level=7, medication_adherence="yes") == 0.2

    def test_boundary_pain_7_partial_adherence_is_mid_risk(self):
        assert _compute_readmission_risk(fever=False, pain_level=7, medication_adherence="partial") == 0.5


# ---------------------------------------------------------------------------
# Agent node tests
# ---------------------------------------------------------------------------

def _mock_llm_json(payload: dict):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(payload)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
async def test_unreachable_logs_and_returns():
    """call_connected=False → status=unreachable, log_outcome called."""
    state = make_state(call_connected=False, messages=[])

    with patch("agents.agent_followup.log_outcome", AsyncMock()) as mock_log:
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    mock_log.assert_called_once()
    assert result["call_outcome"]["status"] == "unreachable"


@pytest.mark.asyncio
async def test_missing_discharge_record_returns_unreachable():
    """No discharge record → graceful close, no crash."""
    state = make_state(call_connected=True, messages=[{"role": "user", "content": "Haan"}])

    with patch("agents.agent_followup.get_discharge_info", AsyncMock(side_effect=ValueError("no record"))):
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    assert result["call_outcome"]["status"] == "unreachable"


@pytest.mark.asyncio
async def test_first_turn_sends_greeting():
    """Empty messages list → greeting sent, checklist not started."""
    state = make_state(call_connected=True, messages=[])

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_INFO)),
        patch("agents.agent_followup.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    assert len(result["messages"]) == 1
    assert "follow-up" in result["messages"][0]["content"].lower()
    assert result.get("call_outcome") is None


@pytest.mark.asyncio
async def test_checklist_incomplete_asks_next_question():
    """LLM says all_answered=False → another question appended."""
    state = make_state(
        call_connected=True,
        messages=[
            {"role": "assistant", "content": "Hello, this is a follow-up call..."},
            {"role": "user", "content": "Haan, thoda dard hai"},
        ],
    )
    mock_client = _mock_llm_json({"reply": "Kya aapko bukhaar hai?", "all_answered": False})

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_INFO)),
        patch("agents.agent_followup.client", mock_client),
    ):
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    assert result["messages"][-1]["content"] == "Kya aapko bukhaar hai?"
    assert result.get("call_outcome") is None


@pytest.mark.asyncio
async def test_high_risk_triggers_escalation():
    """Completed checklist with fever + high pain → escalate_to_doctor called."""
    state = make_state(
        call_connected=True,
        messages=[
            {"role": "assistant", "content": "Hello..."},
            {"role": "user", "content": "Haan bukhaar hai, dard 9 hai"},
        ],
    )
    mock_client = _mock_llm_json({
        "all_answered": True,
        "reply": "Shukriya aapki jaankari ke liye.",
        "fever": True,
        "pain_level": 9,
        "medication_adherence": "yes",
        "additional_concerns": "",
    })

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_INFO)),
        patch("agents.agent_followup.client", mock_client),
        patch("agents.agent_followup.log_outcome", AsyncMock()),
        patch("agents.agent_followup.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    mock_escalate.assert_called_once()
    assert result["call_outcome"]["status"] == "escalated"
    assert result["call_outcome"]["readmission_risk"] == 0.8


@pytest.mark.asyncio
async def test_low_risk_completes_without_escalation():
    """Normal checklist → completed, no escalation."""
    state = make_state(
        call_connected=True,
        messages=[
            {"role": "assistant", "content": "Hello..."},
            {"role": "user", "content": "Theek hoon, koi problem nahi"},
        ],
    )
    mock_client = _mock_llm_json({
        "all_answered": True,
        "reply": "Bahut achha!",
        "fever": False,
        "pain_level": 2,
        "medication_adherence": "yes",
        "additional_concerns": "",
    })

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_INFO)),
        patch("agents.agent_followup.client", mock_client),
        patch("agents.agent_followup.log_outcome", AsyncMock()),
        patch("agents.agent_followup.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        from agents.agent_followup import followup_outbound_node
        result = await followup_outbound_node(state)

    mock_escalate.assert_not_called()
    assert result["call_outcome"]["status"] == "completed"
    assert result["call_outcome"]["readmission_risk"] == 0.2
