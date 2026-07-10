"""Tests for Agent 2 — Voice Intake.

Patches:
- SarvamAI client completions call (_extract_patient_info)
- get_patient_record
- register_patient
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import json as _json
from tests.conftest import make_state


def _make_streaming_response(content: str):
    """Returns a mock that works for both streaming (async for) and non-streaming (.choices[0].message.content)."""
    class _StreamableResponse:
        def __init__(self, c: str) -> None:
            self._content = c
            self.choices = [MagicMock()]
            self.choices[0].message.content = c

        def __aiter__(self):
            return self._aiter()

        async def _aiter(self):
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = self._content
            yield chunk

        def __iter__(self):
            # Sync streaming — the app iterates the stream inside
            # asyncio.to_thread (see _sync_stream_extract).
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = self._content
            yield chunk

    return _StreamableResponse(content)


def _mock_llm(json_payload: dict):
    """Returns a mock client that yields json_payload as the LLM response, supporting streaming."""
    mock_client = MagicMock()
    mock_client.chat.completions.side_effect = [_make_streaming_response(_json.dumps(json_payload))]
    return mock_client


def _mock_intent_classifier():
    """Returns a stub classifier client that returns all-zero scores (below threshold).
    The new fanout issues one call returning 6 scores. All zeros → (None, {}, None) →
    voice_intake falls through to the normal clarification loop."""
    return MagicMock(
        chat=MagicMock(completions=MagicMock(return_value=MagicMock(
            choices=[MagicMock(message=MagicMock(content=(
                '{"book": 0.0, "prescription": 0.0, "lab": 0.0,'
                ' "billing": 0.0, "followup": 0.0, "query": 0.0}'
            )))]
        )))
    )


# ---------------------------------------------------------------------------
# Unit tests for phone assembly helpers (no mocks needed — pure functions)
# ---------------------------------------------------------------------------

def test_digits_from_text_numeric():
    from agents.agent_voice_intake import _digits_from_text
    assert _digits_from_text("9876543210") == "9876543210"

def test_digits_from_text_spaced():
    from agents.agent_voice_intake import _digits_from_text
    assert _digits_from_text("9 8 7 6 5 4 3 2 1 0") == "9876543210"

def test_digits_from_text_word_form():
    from agents.agent_voice_intake import _digits_from_text
    assert _digits_from_text("nine eight seven six five") == "98765"

def test_try_combine_exact_10_across_messages():
    from agents.agent_voice_intake import _try_combine_partial_phone
    messages = [
        {"role": "user", "content": "987654"},
        {"role": "assistant", "content": "Please complete your number"},
        {"role": "user", "content": "3 2 1 0"},
    ]
    assert _try_combine_partial_phone({}, messages) == "9876543210"

def test_try_combine_already_complete():
    from agents.agent_voice_intake import _try_combine_partial_phone
    collected = {"phone": "9876543210"}
    assert _try_combine_partial_phone(collected, []) == "9876543210"

def test_try_combine_too_few_digits_returns_none():
    from agents.agent_voice_intake import _try_combine_partial_phone
    messages = [{"role": "user", "content": "98765"}]   # only 5 digits
    assert _try_combine_partial_phone({}, messages) is None

def test_try_combine_trims_excess_digits():
    from agents.agent_voice_intake import _try_combine_partial_phone
    # 12 digits total → last 10 taken
    messages = [
        {"role": "user", "content": "9876543210 extra 12"},
    ]
    result = _try_combine_partial_phone({}, messages)
    assert result is not None and len(result) == 10


# ---------------------------------------------------------------------------
# Async node tests
# ---------------------------------------------------------------------------

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

    existing_patient = {"id": "existing-uuid", "name": "Ramesh Kumar", "phone": "+919876543210"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted)),
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

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted)),
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
    state = make_state(intake_attempt_count=0)
    unclear = {"intent": None, "reply": "Aap kya chahte hain?"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(unclear)),
        patch("agents.tools.intent_classifier.client", _mock_intent_classifier()),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["intake_attempt_count"] == 1
    assert result["escalation_required"] is False
    assert result["messages"][-1]["content"] == "Aap kya chahte hain?"


@pytest.mark.asyncio
async def test_max_attempts_triggers_escalation():
    """After MAX_INTAKE_ATTEMPTS (3) unclear rounds, escalation_required becomes True."""
    state = make_state(intake_attempt_count=2)  # already at 2, this call pushes to 3
    unclear = {"intent": None, "reply": "Kripya dobara batayein"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(unclear)),
        patch("agents.tools.intent_classifier.client", _mock_intent_classifier()),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["escalation_required"] is True
    assert result["intake_attempt_count"] == 3


@pytest.mark.asyncio
async def test_split_phone_assembled_from_history():
    """Patient gives phone across two turns: '987654' then '3 2 1 0'.
    The second turn's voice_intake must combine both into '9876543210'
    and proceed to DB lookup without asking again."""
    # Turn 1 left phone=None in collected, but put "987654" in messages
    # Turn 2 state reflects that — messages has both user turns
    state = make_state(
        patient_id=None,
        intent="prescription",
        intake_collected={"intent": "prescription", "phone": None},
        messages=[
            {"role": "user", "content": "Mujhe apni dawaai ke baare mein poochna tha"},
            {"role": "assistant", "content": "Bilkul. Aapka naam aur registered phone number bata dijiye?"},
            {"role": "user", "content": "987654"},
            {"role": "assistant", "content": "Number adhura lag raha hai — poora 10 digit ka number bataiye?"},
            {"role": "user", "content": "3 2 1 0"},
        ],
    )

    # LLM still returns phone=null (it only sees the last turn)
    extracted_no_phone = {
        "intent": "prescription",
        "patient_name": None,
        "phone": None,
        "department": None,
        "urgency": "normal",
        "reply": None,
    }
    existing_patient = {"id": "existing-uuid", "name": "Ramesh Kumar", "phone": "9876543210"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted_no_phone)),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=existing_patient)) as mock_lookup,
        patch("agents.agent_voice_intake.register_patient") as mock_register,
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    # Should have assembled "9876543210" from history and looked it up
    mock_register.assert_not_called()
    mock_lookup.assert_called_once_with("9876543210")
    assert result["patient_id"] == "existing-uuid"
    assert result["intent"] == "prescription"


@pytest.mark.asyncio
async def test_partial_phone_less_than_10_digits_waits():
    """If assembled digits total < 10, gate stays open and waits for more."""
    state = make_state(
        patient_id=None,
        intent="prescription",
        intake_collected={"intent": "prescription", "phone": None},
        messages=[
            {"role": "user", "content": "Mujhe dawai ke baare mein poochna tha"},
            {"role": "assistant", "content": "Aapka phone number bataiye?"},
            {"role": "user", "content": "987654"},   # only 6 digits — not enough
        ],
    )

    extracted_partial = {
        "intent": "prescription",
        "patient_name": None,
        "phone": "987654",   # LLM extracts partial
        "department": None,
        "urgency": "normal",
        "reply": "Number adhura lag raha hai",
    }

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted_partial)),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=None)) as mock_lookup,
        patch("agents.agent_voice_intake.register_patient") as mock_register,
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    # Partial phone — lookup must NOT happen, gate stays open
    mock_lookup.assert_not_called()
    mock_register.assert_not_called()
    assert result["patient_id"] is None


@pytest.mark.asyncio
async def test_intent_lab_routes_correctly():
    """LLM extracts intent=lab → patient_id set → route_after_intake will return lab_status."""
    state = make_state(patient_id=None)

    extracted = {
        "intent": "lab",
        "patient_name": "Ramesh",
        "phone": "9876543210",
        "department": None,
        "urgency": "normal",
        "reply": None,
    }
    existing_patient = {"id": "existing-uuid", "name": "Ramesh Kumar", "phone": "9876543210"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted)),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=existing_patient)),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["intent"] == "lab"
    assert result["patient_id"] == "existing-uuid"


@pytest.mark.asyncio
async def test_intent_billing_routes_correctly():
    """LLM extracts intent=billing → patient_id set → route_after_intake will return billing."""
    state = make_state(patient_id=None)

    extracted = {
        "intent": "billing",
        "patient_name": "Sunita",
        "phone": "9876543211",
        "department": None,
        "urgency": "normal",
        "reply": None,
    }
    existing_patient = {"id": "sunita-uuid", "name": "Sunita Devi", "phone": "9876543211"}

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted)),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=existing_patient)),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["intent"] == "billing"
    assert result["patient_id"] == "sunita-uuid"


@pytest.mark.asyncio
async def test_urgent_intent_sets_urgency():
    """Urgency extracted from the LLM is stored on state."""
    state = make_state()

    extracted = {
        "intent": "book",
        "patient_name": "Deepak",
        "phone": "+919111111111",
        "department": "general",
        "urgency": "urgent",
        "reply": None,
    }

    with (
        patch("agents.agent_voice_intake.client", _mock_llm(extracted)),
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value={"id": "uid"})),
    ):
        from agents.agent_voice_intake import voice_intake_node
        result = await voice_intake_node(state)

    assert result["urgency"] == "urgent"
