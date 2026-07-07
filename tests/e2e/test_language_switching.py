"""
E2E Scenario 4 — Language Detection & Mid-Call Switching
=========================================================

Agent 1 (language_router) has three paths per ainvoke():
  A. Redis cache hit  → use cached lang, skip everything else
  B. High STT confidence → use detected_language directly
  C. Low/None confidence → call sarvam_identify_language API

LLM call count per ainvoke() is the same regardless of language:
  [voice_intake_decision, scheduler_or_rx_decision]
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    OPEN_SLOTS_GENERAL,
    PRESCRIPTION_RAMESH,
    intake_book,
    intake_prescription,
    sched_check_slots,
    rx_answer,
)


@pytest.mark.asyncio
async def test_high_confidence_hindi_uses_stt_result():
    """
    detection_confidence=0.93 → Agent 1 uses detected_language=hi-IN directly.
    No identify_language API call needed.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.93)

    with graph_mocks(
        llm_responses=[intake_book(), sched_check_slots()],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "Mujhe appointment chahiye, Ramesh, +919876543210, general",
        )

    print_state("TURN — high confidence Hindi", state)

    assert state["lang_code"] == "hi-IN"
    assert state["tts_voice"] == "priya"
    assert state["intent"] == "book"


@pytest.mark.asyncio
async def test_low_confidence_falls_back_to_identify_api_returns_marathi():
    """
    detection_confidence=None → Agent 1 calls sarvam_identify_language.
    Mock returns "mr-IN" → lang_code=mr-IN, tts_voice=kavya.
    """
    state = fresh_state(detected_language=None, detection_confidence=None)

    with graph_mocks(
        llm_responses=[intake_prescription(), rx_answer("तुम्हाला Amlodipine घ्यायची आहे.")],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
        detected_lang="mr-IN",
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "माझ्या औषधांबद्दल सांगा, Ramesh, +919876543210",
        )

    print_state("TURN — Marathi via identify API", state)

    assert state["lang_code"] == "mr-IN"
    assert state["tts_voice"] == "kavya"


@pytest.mark.asyncio
async def test_repeat_caller_cached_language_overrides_stt():
    """
    Redis cache has lang_pref=mr-IN for this patient.
    Even though detected_language=hi-IN (STT result), Agent 1 uses the
    cached value and never calls sarvam_identify_language.
    """
    state = fresh_state(
        patient_id="patient-ramesh-uuid",
        detected_language="hi-IN",
        detection_confidence=0.88,
    )

    with graph_mocks(
        llm_responses=[intake_book(), sched_check_slots()],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
        cached_lang="mr-IN",   # ← Redis cache says Marathi
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "Appointment chahiye, Ramesh, +919876543210, general",
        )

    print_state("TURN — repeat caller, cached Marathi overrides STT", state)

    assert state["lang_code"] == "mr-IN"
    assert state["tts_voice"] == "kavya"


@pytest.mark.asyncio
async def test_language_switch_across_turns():
    """
    Turn 1: patient speaks Hindi → hi-IN set.
    Turn 2: state["detection_confidence"] set to None (livekit would update
    this from the new STT result) → identify API called → mr-IN returned.
    Message history carries over — Agent 2 in Turn 2 sees full history.
    """
    # Turn 1 — Hindi
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.92)

    with graph_mocks(
        llm_responses=[intake_book(), sched_check_slots()],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
        detected_lang="hi-IN",
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "Mujhe appointment chahiye, Ramesh, +919876543210, general",
        )

    print_state("TURN 1 — Hindi", state)
    assert state["lang_code"] == "hi-IN"
    msg_count_t1 = len(state["messages"])

    # Simulate livekit updating detection from next utterance
    state["detected_language"] = "mr-IN"
    state["detection_confidence"] = None   # triggers identify API

    # Turn 2 — Marathi (patient switched language)
    with graph_mocks(
        llm_responses=[intake_book(), sched_check_slots()],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
        detected_lang="mr-IN",
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "मला पहिला स्लॉट हवा आहे",
        )

    print_state("TURN 2 — switched to Marathi", state)

    assert state["lang_code"] == "mr-IN"
    assert state["tts_voice"] == "kavya"
    # Full message history accumulated across both turns
    assert len(state["messages"]) > msg_count_t1
