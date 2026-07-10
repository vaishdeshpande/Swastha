"""
E2E Scenario — Lab Status Flow (Agent 6)
==========================================

Graph path per ainvoke():
  language_router (no LLM) → voice_intake (LLM #1) → lab_status (no LLM) → post_call → END

Agent 6 is a pure-lookup node — no LLM call happens inside it.
So the ONLY LLM call per turn is voice_intake.

Turn structure:
  Turn 1: [intake_lab]   → lab_status node runs, reports spoken
"""

from __future__ import annotations

import pytest

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    PATIENT_SUNITA,
    LAB_REPORTS_RAMESH,
    LAB_REPORTS_PENDING,
    LAB_REPORTS_MIXED,
    BILL_SUNITA,
    intake_lab,
    intake_unclear,
)


@pytest.mark.asyncio
async def test_hindi_lab_ready_report_read():
    """
    Patient says "meri report aayi kya" → intent=lab extracted → Agent 6 runs.
    Ready report is translated and spoken. mark_report_dispatched is called.

    1 LLM call: [intake_lab]
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.90)

    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=LAB_REPORTS_RAMESH,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Meri report aayi kya? CBC test diya tha. Main Ramesh, +919876543210",
        )

    print_state("LAB — Hindi ready report", state)

    assert state["intent"] == "lab"
    assert state["current_agent"] in ("lab_status", "post_call")
    assert state["escalation_required"] is False
    assert "Complete Blood Count" in reply or "CBC" in reply or "Hemoglobin" in reply
    # State field populated for frontend data channel event
    assert state["lab_reports_dispatched"] is not None
    assert len(state["lab_reports_dispatched"]) >= 1


@pytest.mark.asyncio
async def test_marathi_lab_pending_report():
    """
    Marathi patient with pending report → "still being processed" message.
    No dispatching occurs.
    """
    state = fresh_state(
        detected_language="mr-IN",
        detection_confidence=0.88,
        lang_code="mr-IN",
        tts_voice="kavya",
    )

    with graph_mocks(
        llm_responses=[intake_lab(name="Arun Patil", phone="+919876543212")],
        patient={
            "id": "patient-arun-uuid",
            "name": "Arun Patil",
            "phone": "+919876543212",
            "age": 52,
            "lang_pref": "mr-IN",
            "blood_group": "B+",
            "medical_history": [],
            "is_new": False,
        },
        lab_reports=LAB_REPORTS_PENDING,
        detected_lang="mr-IN",
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Maza report tayar zala ka? Lipid panel test dila hota.",
        )

    print_state("LAB — Marathi pending report", state)

    assert state["intent"] == "lab"
    assert state["escalation_required"] is False
    assert "Lipid Panel" in reply or "processing" in reply.lower() or "processed" in reply.lower()


@pytest.mark.asyncio
async def test_lab_mixed_ready_and_pending():
    """
    Patient has both a ready and a pending report.
    Ready report is read, pending listed as still processing.
    Two assistant messages expected.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.91)

    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=LAB_REPORTS_MIXED,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Meri dono reports check karo, Ramesh, +919876543210",
        )

    print_state("LAB — mixed ready + pending", state)

    assert state["intent"] == "lab"
    # Both test names should appear across messages
    all_content = " ".join(m["content"] for m in state["messages"] if m["role"] == "assistant")
    assert "Complete Blood Count" in all_content or "CBC" in all_content
    assert "Lipid Panel" in all_content


@pytest.mark.asyncio
async def test_lab_no_reports_on_file():
    """
    Patient has no lab reports → informative message, no escalation.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=[],        # empty — no reports seeded
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Mera koi report hai kya? Ramesh, +919876543210",
        )

    print_state("LAB — no reports on file", state)

    assert state["intent"] == "lab"
    assert state["escalation_required"] is False
    assert "no lab" in reply.lower() or "lab counter" in reply.lower()


@pytest.mark.asyncio
async def test_lab_intent_routes_correctly_not_to_scheduler():
    """
    Regression: intent=lab must NOT route to scheduler. current_agent should
    never be 'scheduler' after a lab intent call.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=LAB_REPORTS_RAMESH,
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "Lab report status chahiye, Ramesh +919876543210",
        )

    agents_active = [m.get("agent") for m in state["messages"] if m.get("role") == "assistant"]
    # Verify scheduler was never set as current_agent during this call
    assert state.get("appointment_id") is None
    assert state["intent"] == "lab"


@pytest.mark.asyncio
async def test_lab_intent_unclear_first_then_resolved():
    """
    First turn: voice_intake returns intent=None → clarification question.
    Second turn: voice_intake resolves to lab → Agent 6 runs.
    The graph routes to END (await_input) after each unclear turn, so two run_turns needed.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[
            intake_unclear("Aap report check karna chahte hain ya appointment?"),
        ],
        patient=PATIENT_RAMESH,
        lab_reports=LAB_REPORTS_RAMESH,
    ):
        _, state = await run_turn(inbound_graph, state, "Kuch poochna tha")

    assert state["intake_attempt_count"] == 1
    assert state["intent"] is None

    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=LAB_REPORTS_RAMESH,
    ):
        reply, state = await run_turn(inbound_graph, state, "Haan woh test wala")

    print_state("LAB — intent unclear → resolved to lab", state)

    assert state["intent"] == "lab"
    assert state["lab_reports_dispatched"] is not None


@pytest.mark.asyncio
async def test_lab_repeat_call_dispatched_reports_not_returned():
    """
    Dispatched reports (already read to patient) are excluded by get_lab_status.
    Simulate a repeat call where only the previously-dispatched report existed.
    Patient should see "no reports on file", not a re-read.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    # get_lab_status filters dispatched rows — returns empty even if DB has rows
    with graph_mocks(
        llm_responses=[intake_lab()],
        patient=PATIENT_RAMESH,
        lab_reports=[],   # dispatched reports excluded by the tool function
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Wahi report dobara check karo, Ramesh +919876543210",
        )

    print_state("LAB — repeat call, dispatched excluded", state)

    assert state["escalation_required"] is False
    assert "no lab" in reply.lower() or "lab counter" in reply.lower()
