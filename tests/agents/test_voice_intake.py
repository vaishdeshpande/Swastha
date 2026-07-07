"""Tests for Agent 2 — Voice Intake.

Patches:
- SarvamAI client completions call (_extract_patient_info)
- get_patient_record
- register_patient
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_state


def _mock_llm(json_payload: dict):
    """Returns a mock that mimics client.chat.completions(...)."""
    mock_response = MagicMock()
    mock_response.choices[0].message.content = str(json_payload).replace("'", '"')
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response
    return mock_client


@pytest.mark.asyncio
async def test_known_patient_intent_book():
    """Extracted intent=book for a known patient sets state correctly."""
    state = make_state(
        patient_id=None,
        messages=[{"role": "user", "content": "Mujhe cardiology appointment chahiye, main Ramesh hoon, +919876543210"}],
    )

    extracted = {
        "intent": "book",
        "patient_name": "Ramesh Kumar",
        "phone": "+919876543210",
        "department": "cardiology",
        "urgency": "normal",
        "reply": None,
    }

    import json
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(extracted)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response

    existing_patient = {"id": "existing-uuid", "name": "Ramesh Kumar", "phone": "+919876543210"}

    with (
        patch("agents.agent_voice_intake.client", mock_client),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=existing_patient)),
        patch("agents.agent_voice_intake.register_patient") as mock_register,
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    mock_register.assert_not_called()
    assert result["intent"] == "book"
    assert result["department"] == "cardiology"
    assert result["patient_id"] == "existing-uuid"
    assert result["is_new_patient"] is False
    assert result["escalation_required"] is False


@pytest.mark.asyncio
async def test_new_patient_registered_silently():
    """When get_patient_record returns None, register_patient is called
    and is_new_patient is set to True. Patient hears no pause."""
    import json
    state = make_state(patient_id=None)

    extracted = {
        "intent": "prescription",
        "patient_name": "Sunita Devi",
        "phone": "+919999999999",
        "department": "general",
        "urgency": "normal",
        "age": 38,
        "reply": None,
    }
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(extracted)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response

    with (
        patch("agents.agent_voice_intake.client", mock_client),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=None)),
        patch("agents.agent_voice_intake.register_patient", AsyncMock(return_value="new-uuid")),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["patient_id"] == "new-uuid"
    assert result["is_new_patient"] is True
    assert result["intent"] == "prescription"


@pytest.mark.asyncio
async def test_unclear_intent_increments_attempt_count():
    """When LLM returns intent=None, attempt_count increments and
    no escalation happens on the first round."""
    import json
    state = make_state(intake_attempt_count=0)

    unclear = {"intent": None, "reply": "Aap kya chahte hain?"}
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(unclear)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response

    with patch("agents.agent_voice_intake.client", mock_client):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["intake_attempt_count"] == 1
    assert result["escalation_required"] is False
    assert result["messages"][-1]["content"] == "Aap kya chahte hain?"


@pytest.mark.asyncio
async def test_max_attempts_triggers_escalation():
    """After MAX_INTAKE_ATTEMPTS (3) unclear rounds, escalation_required becomes True."""
    import json
    state = make_state(intake_attempt_count=2)  # already at 2, this call pushes to 3

    unclear = {"intent": None, "reply": "Kripya dobara batayein"}
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(unclear)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response

    with patch("agents.agent_voice_intake.client", mock_client):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["escalation_required"] is True
    assert result["intake_attempt_count"] == 3


@pytest.mark.asyncio
async def test_urgent_intent_sets_urgency():
    """Urgency extracted from the LLM is stored on state."""
    import json
    state = make_state()

    extracted = {
        "intent": "book",
        "patient_name": "Deepak",
        "phone": "+919111111111",
        "department": "general",
        "urgency": "urgent",
        "reply": None,
    }
    mock_response = MagicMock()
    mock_response.choices[0].message.content = json.dumps(extracted)
    mock_client = MagicMock()
    mock_client.chat.completions.return_value = mock_response

    with (
        patch("agents.agent_voice_intake.client", mock_client),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value={"id": "uid"})),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["urgency"] == "urgent"
