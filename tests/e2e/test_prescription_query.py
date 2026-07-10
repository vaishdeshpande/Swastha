"""
E2E Scenario 2 — Prescription Query
=====================================

LLM calls per ainvoke():
  [voice_intake_rx, rx_agent_decision]   = 2 calls (happy path)
  [voice_intake_rx]                      = 1 call if get_prescription raises → escalates before rx LLM
"""

from __future__ import annotations

import pytest

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    PATIENT_SUNITA,
    PRESCRIPTION_RAMESH,
    intake_prescription,
    rx_answer,
    rx_escalate,
)


@pytest.mark.asyncio
async def test_hindi_prescription_query_answered():
    """
    Single-turn prescription query. Agent 4 fetches prescription,
    translates notes (passthrough in tests), returns medicine schedule.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.95)

    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_answer("Aapko Amlodipine 5mg subah leni hai aur Aspirin 75mg dopahar mein."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Meri dawai ka schedule kya hai? Ramesh hoon, +919876543210",
        )

    print_state("TURN — Hindi prescription query", state)

    assert state["lang_code"] == "hi-IN"
    assert state["tts_voice"] == "priya"
    assert state["intent"] == "prescription"
    assert state["escalation_required"] is False
    assert "Amlodipine" in reply


@pytest.mark.asyncio
async def test_marathi_prescription_uses_kavya_voice():
    """
    Same query detected as Marathi via sarvam_identify_language.
    Agent 1 sets lang_code=mr-IN and tts_voice=kavya.
    """
    state = fresh_state(
        detected_language=None,
        detection_confidence=None,   # triggers identify_language API
    )

    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_answer("तुम्हाला Amlodipine 5mg सकाळी घ्यायची आहे."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
        detected_lang="mr-IN",
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "माझ्या औषधांबद्दल माहिती हवी, Ramesh, +919876543210",
        )

    print_state("TURN — Marathi prescription query", state)

    assert state["lang_code"] == "mr-IN"
    assert state["tts_voice"] == "kavya"
    assert state["intent"] == "prescription"
    assert state["escalation_required"] is False


@pytest.mark.asyncio
async def test_out_of_scope_question_escalates():
    """
    Agent 4 LLM returns escalate=True → human handoff.
    Patient hears transfer message.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_escalate("Dawai band karne ke liye doctor se milein."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Kya main dawai permanently band kar sakta hoon? Ramesh, +919876543210",
        )

    print_state("TURN — out-of-scope prescription question", state)

    assert state["escalation_required"] is True
    assert "doctor" in reply.lower() or "staff" in reply.lower()


@pytest.mark.asyncio
async def test_no_prescription_escalates_without_rx_llm_call():
    """
    get_prescription raises ValueError before the Agent 4 LLM is called.
    Only voice_intake LLM call happens (1 total), then escalation.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            intake_prescription(name="Sunita Devi", phone="+919876543211"),
            # NO rx_answer here — ValueError fires before LLM is called
        ],
        patient=PATIENT_SUNITA,
        prescription=None,    # ← triggers ValueError in get_prescription
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Meri dawai ki jaankari chahiye, Sunita hoon, +919876543211",
        )

    print_state("TURN — no prescription on file", state)

    assert state["escalation_required"] is True
    assert "No prescription" in state["escalation_reason"]
    # Transfer message spoken — patient not left in silence
    assert reply


@pytest.mark.asyncio
async def test_split_phone_across_turns_prescription():
    """
    Scenario from live conversation: patient gives phone in two parts.
    Turn 1: "Meri dawai ke baare mein poochna tha" → intake asks for phone
    Turn 2: "987654" → intake asks for rest
    Turn 3: "3 2 1 0" → _try_combine_partial_phone assembles "9876543210" →
             patient matched → prescription fetched.

    LLM calls:
      Turn 1: [intake_prescription_no_phone]   → 1 call, awaits phone
      Turn 2: [intake_prescription_no_phone]   → 1 call, partial phone, still awaits
      Turn 3: [intake_prescription]            → 1 call, assembly happens in Python,
              [rx_answer]                      → 1 call, prescription returned
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.92)

    _no_phone = {
        "intent": "prescription",
        "patient_name": None,
        "phone": None,
        "department": None,
        "urgency": "normal",
        "reply": "Bilkul. Aapka naam aur registered phone number bata dijiye?",
    }
    _partial_phone = {
        "intent": "prescription",
        "patient_name": None,
        "phone": "987654",   # LLM picks up the partial but it's < 10 digits
        "department": None,
        "urgency": "normal",
        "reply": "Number adhura lag raha hai — poora 10 digit ka number bataiye?",
    }

    # Turn 1 — intent captured, phone not given
    with graph_mocks(
        llm_responses=[_no_phone],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply1, state = await run_turn(
            inbound_graph, state,
            "Meri dawai ke baare mein poochna tha",
        )

    assert state["intent"] == "prescription"
    assert state["patient_id"] is None  # no phone yet

    # Turn 2 — partial phone given
    with graph_mocks(
        llm_responses=[_partial_phone],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply2, state = await run_turn(inbound_graph, state, "987654")

    assert state["patient_id"] is None  # still waiting — partial

    # Turn 3 — rest of phone given: Python helper assembles 9876543210
    with graph_mocks(
        llm_responses=[
            # LLM returns phone=None (only sees "3 2 1 0" in isolation)
            {**_no_phone, "reply": None},
            rx_answer("Aapko Amlodipine 5mg subah leni hai."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply3, state = await run_turn(inbound_graph, state, "3 2 1 0")

    print_state("TURN 3 — split phone assembled → prescription returned", state)

    assert state["patient_id"] == PATIENT_RAMESH["id"]
    assert state["intent"] == "prescription"
    assert state["escalation_required"] is False
    assert "Amlodipine" in reply3


@pytest.mark.asyncio
async def test_prescription_multi_turn_history_grows():
    """
    Two consecutive prescription questions. Message history accumulates
    across turns — Agent 4 in Turn 2 sees the full Turn 1 conversation.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    # Turn 1
    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_answer("Aapko Amlodipine 5mg subah leni hai."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply1, state = await run_turn(
            inbound_graph, state,
            "Meri dawai ka schedule kya hai? Ramesh +919876543210",
        )

    assert "Amlodipine" in reply1
    msg_count_after_t1 = len(state["messages"])

    # Turn 2 — follow-up question
    with graph_mocks(
        llm_responses=[
            intake_prescription(),
            rx_answer("Aspirin ko khaane ke baad lena behtar hai."),
        ],
        patient=PATIENT_RAMESH,
        prescription=PRESCRIPTION_RAMESH,
    ):
        reply2, state = await run_turn(
            inbound_graph, state,
            "Aur Aspirin kab leni chahiye?",
        )

    print_state("TURN 2 — prescription follow-up", state)

    assert len(state["messages"]) > msg_count_after_t1   # history grew
    assert "Aspirin" in reply2
