"""
E2E Scenario 1 — Hindi Appointment Booking
============================================

What the graph actually does per ainvoke():
  language_router (no LLM) → voice_intake (LLM #1) → scheduler (LLM #2) → post_call → END

If voice_intake loops (intent=None), extra LLM calls happen before scheduler.
All LLM responses for the full ainvoke() must be passed in order.

Turn 1 ("Mujhe appointment book karni hai, main Ramesh hoon..."):
  LLM #1 — voice_intake  → intent="book", patient fields extracted
  LLM #2 — scheduler     → action="check_slots"
  State after: offered_slots set, messages has slot list

Turn 2 ("Pehla slot theek hai"):
  LLM #1 — voice_intake (re-runs every turn) → intent="book" again from history
  LLM #2 — scheduler     → action="confirm_booking", chosen_slot_id="slot-g1"
  State after: appointment_id set, offered_slots=None
"""

from __future__ import annotations

import pytest

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    OPEN_SLOTS_GENERAL,
    BOOKING_CONFIRMATION,
    intake_book,
    intake_unclear,
    sched_check_slots,
    sched_confirm,
    sched_cancel,
    sched_clarify,
)


@pytest.mark.asyncio
async def test_full_hindi_appointment_booking_two_turns():
    """
    Patient gives all info in Turn 1. Scheduler offers slots.
    Patient picks slot in Turn 2. Appointment confirmed.

    LLM calls per turn:
      Turn 1: [voice_intake_book, sched_check_slots]  = 2 calls
      Turn 2: [voice_intake_book, sched_confirm]      = 2 calls
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.92)

    # ── Turn 1: Full info given — intent extracted immediately ─────────────
    with graph_mocks(
        llm_responses=[
            intake_book(),             # voice_intake: extracts intent=book on 1st try
            sched_check_slots(),       # scheduler: offers available slots
        ],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply1, state = await run_turn(
            inbound_graph, state,
            "Mujhe appointment book karni hai. Main Ramesh hoon, +919876543210, general doctor chahiye",
        )

    print_state("TURN 1 — patient gives full details", state)

    assert state["lang_code"] == "hi-IN"
    assert state["tts_voice"] == "priya"
    assert state["intent"] == "book"
    assert state["department"] == "general"
    assert state["patient_id"] == PATIENT_RAMESH["id"]
    assert state["is_new_patient"] is False
    assert state["offered_slots"] is not None
    assert len(state["offered_slots"]) <= 3
    assert state["escalation_required"] is False
    assert "Dr. Priya Sharma" in reply1

    # ── Turn 2: Patient picks first slot ──────────────────────────────────
    with graph_mocks(
        llm_responses=[
            # voice_intake is SKIPPED on this turn (route_after_language: intent
            # + patient_id already resolved) — scheduler LLM is the only call.
            sched_confirm("slot-g1"), # scheduler: patient said yes to slot-g1
        ],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
        booking=BOOKING_CONFIRMATION,
    ):
        reply2, state = await run_turn(inbound_graph, state, "Pehla slot theek hai")

    print_state("TURN 2 — slot confirmed", state)

    assert state["appointment_id"] == "appt-uuid-001"
    assert state["offered_slots"] is None      # cleared after booking
    assert state["escalation_required"] is False
    assert "Dr. Priya Sharma" in reply2
    assert "2026-07-10" in reply2


@pytest.mark.asyncio
async def test_intent_unclear_once_then_resolved():
    """
    Turn 1: patient is vague → voice_intake returns intent=None → clarification asked → await_input.
    Turn 2: patient clarifies → intent=book resolved → scheduler runs.

    Graph routes to END (await_input) after each unclear turn, so two run_turns are needed.
    2 LLM calls total across 2 turns: [intake_unclear] then [intake_book, sched_check_slots]
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    # Turn 1 — vague utterance, get clarification
    with graph_mocks(
        llm_responses=[intake_unclear("Kya aap appointment chahte hain ya kuch aur?")],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        _, state = await run_turn(inbound_graph, state, "Haan woh doctor wali cheez")

    assert state["intake_attempt_count"] == 1
    assert state["intent"] is None

    # Turn 2 — patient clarifies with full details
    with graph_mocks(
        llm_responses=[intake_book(), sched_check_slots()],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Appointment chahiye, main Ramesh hoon, +919876543210",
        )

    print_state("TURN — intent unclear once then resolved", state)

    assert state["intent"] == "book"
    assert state["offered_slots"] is not None
    assert state["intake_attempt_count"] == 1


@pytest.mark.asyncio
async def test_intent_unclear_3_times_escalates():
    """
    Patient is completely unclear across 3 consecutive turns.
    Each turn increments intake_attempt_count. On the 3rd unclear turn,
    escalation_required is set and human_handoff runs.

    3 separate run_turn() calls — one per patient utterance.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    for clarify_reply in [
        "Main aapki baat samajh nahi paaya",
        "Kripya dobara batayein",
    ]:
        with graph_mocks(
            llm_responses=[intake_unclear(clarify_reply)],
            patient=PATIENT_RAMESH,
        ):
            _, state = await run_turn(inbound_graph, state, "Haan woh cheez chahiye")

    assert state["intake_attempt_count"] == 2
    assert state["escalation_required"] is False

    # Third unclear turn → escalation
    with graph_mocks(
        llm_responses=[intake_unclear("Kya aap appointment chahte hain?")],
        patient=PATIENT_RAMESH,
    ):
        reply, state = await run_turn(inbound_graph, state, "Haan woh cheez chahiye")

    print_state("3 unclear turns → escalation", state)

    assert state["escalation_required"] is True
    assert state["intake_attempt_count"] == 3
    assert reply  # patient hears transfer message from human_handoff


@pytest.mark.asyncio
async def test_urgent_appointment_booking():
    """
    Patient says "bahut dard hai" → urgency="urgent" extracted.
    Everything else flows normally.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            {**intake_book(), "urgency": "urgent"},
            sched_check_slots(),
        ],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Bahut dard hai, jaldi doctor chahiye. Ramesh hoon, +919876543210, general",
        )

    print_state("TURN — urgent appointment", state)

    assert state["urgency"] == "urgent"
    assert state["intent"] == "book"
    assert state["offered_slots"] is not None


@pytest.mark.asyncio
async def test_new_patient_silently_registered():
    """
    Patient not in DB → voice_intake calls register_patient silently.
    Conversation continues to scheduler without audible pause.
    patient_id is the newly created UUID.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.91)

    with graph_mocks(
        llm_responses=[
            {**intake_book(name="Nayi Patient", phone="+919000000001"), "age": 30},
            sched_check_slots(),
        ],
        patient=None,                       # ← not in DB
        new_patient_id="new-patient-uuid-999",
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Appointment chahiye, pehli baar. Nayi Patient, +919000000001, general",
        )

    print_state("TURN — new patient registered silently", state)

    assert state["patient_id"] == "new-patient-uuid-999"
    assert state["is_new_patient"] is True
    assert state["intent"] == "book"
    # Patient hears slot options, not a registration confirmation
    assert state["offered_slots"] is not None


@pytest.mark.asyncio
async def test_appointment_reschedule():
    """
    Patient already has an appointment and wants to reschedule.
    Scheduler should cancel the old one and offer new slots.
    cancel_appointment is called, offered_slots repopulated.
    """
    state = fresh_state(
        detected_language="hi-IN",
        detection_confidence=0.9,
        # Skip the intake phase: state already has intent+patient from a prior turn
        intent="book",
        department="general",
        patient_id=PATIENT_RAMESH["id"],
        appointment_id="old-appt-uuid",
        messages=[{"role": "user", "content": "Mera purana appointment theek nahi, badalna hai"}],
    )

    with graph_mocks(
        llm_responses=[
            # voice_intake is SKIPPED (intent + patient_id already in state).
            {"action": "reschedule", "cancel_appointment_id": "old-appt-uuid", "distress": False},
        ],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply, state = await run_turn(inbound_graph, state, "Appointment reschedule karni hai")

    print_state("TURN — reschedule", state)

    assert state["appointment_id"] is None      # cleared by cancel
    assert state["offered_slots"] is not None    # new options offered


@pytest.mark.asyncio
async def test_slots_empty_falls_back_to_next_available():
    """
    check_available_slots returns [] for the requested date.
    Scheduler should call get_next_available and still present options.
    """
    from unittest.mock import AsyncMock, patch
    import agents.agent_scheduler as sched_mod

    state = fresh_state(detected_language="hi-IN", detection_confidence=0.88)

    with graph_mocks(
        llm_responses=[
            intake_book(dept="cardiology"),
            sched_check_slots("2026-07-09"),
        ],
        patient=PATIENT_RAMESH,
        slots=[],           # ← empty for check_available_slots
    ):
        # Override get_next_available separately to return slots
        with patch.object(sched_mod, "get_next_available", AsyncMock(return_value=OPEN_SLOTS_GENERAL)):
            reply, state = await run_turn(
                inbound_graph, state,
                "Appointment chahiye kal ka, cardiology, Ramesh +919876543210",
            )

    print_state("TURN — no slots on date, fallback to next_available", state)

    assert state["offered_slots"] is not None
    assert state["intent"] == "book"


@pytest.mark.asyncio
async def test_distress_mid_scheduling_triggers_escalation():
    """
    Scheduler LLM returns distress=True → scheduler_node sets
    escalation_required=True → human_handoff fires.
    No appointment is booked.
    """
    state = fresh_state(
        detected_language="hi-IN",
        detection_confidence=0.9,
        intent="book",
        department="general",
        patient_id=PATIENT_RAMESH["id"],
        messages=[{"role": "user", "content": "Kuch samajh nahi aa raha"}],
    )

    with graph_mocks(
        llm_responses=[
            # voice_intake is SKIPPED (intent + patient_id already in state).
            {"action": "clarify", "reply": "Kya main madad kar sakta hoon?", "distress": True},
        ],
        patient=PATIENT_RAMESH,
        slots=OPEN_SLOTS_GENERAL,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Mujhe kuch samajh nahi aa raha",
        )

    print_state("TURN — distress → escalation", state)

    assert state["escalation_required"] is True
    assert state["appointment_id"] is None
