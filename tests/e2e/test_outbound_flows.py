"""
E2E Scenario 5 — Outbound Graph (cron-driven jobs)
====================================================

outbound_graph.ainvoke(initial_state) — same call as api/main.py's cron.

initial_state built by api/main.py:
  {
    "patient_id": "...",
    "lang_code": "hi-IN",
    "tts_voice": "priya",
    "job_type": "confirmation" | "rx_reminder" | "followup",
    "messages": [],
    "current_agent": "route_job",
    ...
  }

The outbound graph has NO voice_intake step. Each job type routes to a
different agent node, so there is no common LLM call ordering.
Tests patch each agent module's client directly.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agents.graph import outbound_graph
from tests.e2e.helpers import print_state
from tests.e2e.mocks import (
    PATIENT_RAMESH,
    PATIENT_SUNITA,
    OPEN_SLOTS_GENERAL,
    BOOKING_CONFIRMATION,
    PRESCRIPTION_RAMESH,
    DISCHARGE_SUNITA,
    llm_sequence,
)


def outbound_state(patient_id: str, lang_code: str, job_type: str) -> dict:
    """Minimal state that api/main.py passes to outbound_graph."""
    return {
        "lang_code": lang_code,
        "tts_voice": "priya" if lang_code == "hi-IN" else "kavya",
        "tts_model": "bulbul:v3",
        "detected_language": None,
        "detection_confidence": None,
        "patient_id": patient_id,
        "patient_name": None,
        "is_new_patient": False,
        "intent": None,
        "department": None,
        "urgency": "normal",
        "intake_attempt_count": 0,
        "messages": [],
        "current_agent": "route_job",
        "escalation_required": False,
        "escalation_reason": None,
        "call_id": f"outbound-{job_type}-test",
        "call_recording_path": None,
        "call_outcome": None,
        "call_start_time": "2026-07-06T10:00:00+00:00",
        "offered_slots": None,
        "appointment_id": "appt-uuid-001",
        "job_type": job_type,
        "call_connected": True,
    }


# ---------------------------------------------------------------------------
# Appointment confirmation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_confirmation_patient_agrees():
    """
    job_type=confirmation → scheduler_outbound_node.
    Patient agrees → appointment confirmed in DB.
    """
    state = outbound_state(PATIENT_RAMESH["id"], "hi-IN", "confirmation")

    # scheduler_outbound sends reminder script, then reads patient reply.
    # Patient says yes → scheduler LLM returns action=clarify (no cancel/reschedule)
    # → confirm_appointment called.
    llm = llm_sequence({"action": "clarify", "distress": False})

    with (
        patch("agents.agent_scheduler.client", llm),
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_scheduler.confirm_appointment", AsyncMock(return_value=True)) as mock_confirm,
        patch("agents.agent_scheduler.check_available_slots", AsyncMock(return_value=OPEN_SLOTS_GENERAL)),
        patch("agents.agent_scheduler.get_next_available", AsyncMock(return_value=OPEN_SLOTS_GENERAL)),
        patch("agents.agent_scheduler.book_slot", AsyncMock(return_value=BOOKING_CONFIRMATION)),
        patch("agents.agent_scheduler.cancel_appointment", AsyncMock(return_value=True)),
    ):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND — appointment confirmation (patient confirmed)", result)

    mock_confirm.assert_called_once_with("appt-uuid-001")
    assert result["call_outcome"]["confirmed"] is True
    # Reminder script was spoken
    reminder_msg = result["messages"][0]["content"]
    assert "appointment" in reminder_msg.lower() or "reminder" in reminder_msg.lower()


@pytest.mark.asyncio
async def test_outbound_confirmation_patient_cancels():
    """
    scheduler_outbound_node sends reminder + immediately calls LLM to read the patient's reply.
    Pre-populate messages with patient's "I can't come" utterance.

    Single ainvoke, 2 LLM calls:
      LLM #1 (scheduler_outbound): reads "Nahi aa sakta" → returns cancel
      LLM #2 (scheduler_node): offers new slots after cancel
    """
    state = outbound_state(PATIENT_RAMESH["id"], "hi-IN", "confirmation")
    state["messages"].append({"role": "user", "content": "Nahi aa sakta, koi aur din milega?"})

    # LLM #1 (scheduler_outbound): reads "Nahi aa sakta" → cancel → routes to scheduler_node
    # LLM #2 (scheduler_node): called again → cancel → actually calls cancel_appointment
    llm = llm_sequence(
        {"action": "cancel", "cancel_appointment_id": "appt-uuid-001", "distress": False},
        {"action": "cancel", "cancel_appointment_id": "appt-uuid-001", "distress": False},
    )
    cancel_mock = AsyncMock(return_value=True)

    with (
        patch("agents.agent_scheduler.client", llm),
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_scheduler.cancel_appointment", cancel_mock),
        patch("agents.agent_scheduler.check_available_slots", AsyncMock(return_value=OPEN_SLOTS_GENERAL)),
        patch("agents.agent_scheduler.get_next_available", AsyncMock(return_value=OPEN_SLOTS_GENERAL)),
        patch("agents.agent_scheduler.book_slot", AsyncMock(return_value=BOOKING_CONFIRMATION)),
        patch("agents.agent_scheduler.confirm_appointment", AsyncMock(return_value=True)),
    ):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND — appointment confirmation (patient cancelled)", result)

    cancel_mock.assert_called()
    assert result["appointment_id"] is None


# ---------------------------------------------------------------------------
# Medication reminder
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_rx_reminder_medicine_names_spoken():
    """
    job_type=rx_reminder → prescription_outbound_node.
    Medicine names in the script, mark_reminder_sent called.
    """
    state = outbound_state(PATIENT_RAMESH["id"], "hi-IN", "rx_reminder")

    with (
        patch("agents.agent_prescription.get_prescription", AsyncMock(return_value=PRESCRIPTION_RAMESH)),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_prescription.mark_reminder_sent", AsyncMock()) as mock_mark,
    ):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND — rx reminder", result)

    mock_mark.assert_called_once_with(PATIENT_RAMESH["id"])
    assert result["call_outcome"]["reminder_sent"] is True
    spoken = result["messages"][-1]["content"]
    assert "Amlodipine" in spoken
    assert "Aspirin" in spoken


@pytest.mark.asyncio
async def test_outbound_rx_reminder_no_prescription_closes_gracefully():
    """
    Prescription was deleted after the job was scheduled.
    Node catches ValueError → reminder_sent=False → job closes cleanly.
    """
    state = outbound_state(PATIENT_RAMESH["id"], "hi-IN", "rx_reminder")

    with patch("agents.agent_prescription.get_prescription",
               AsyncMock(side_effect=ValueError("no rx"))):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND — rx reminder, no prescription", result)

    assert result["call_outcome"]["reminder_sent"] is False
    assert result["call_outcome"]["reason"] == "no prescription on file"


# ---------------------------------------------------------------------------
# Post-discharge follow-up
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_outbound_followup_greeting_first_turn():
    """
    First ainvoke with empty messages → greeting sent, checklist not started.
    """
    state = outbound_state(PATIENT_SUNITA["id"], "hi-IN", "followup")

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_SUNITA)),
        patch("agents.agent_followup.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND FOLLOWUP — greeting turn", result)

    assert len(result["messages"]) == 1
    assert "follow-up" in result["messages"][0]["content"].lower()
    assert result.get("call_outcome") is None


@pytest.mark.asyncio
async def test_outbound_followup_low_risk_completed():
    """
    Patient reports normal recovery (fever=False, pain=2, adherent).
    readmission_risk=0.2 → status=completed, no doctor escalation.
    """
    state = outbound_state(PATIENT_SUNITA["id"], "hi-IN", "followup")

    checklist = {
        "all_answered": True,
        "reply": "Bahut achha, jaldi theek ho jaayein!",
        "fever": False,
        "pain_level": 2,
        "medication_adherence": "yes",
        "additional_concerns": "",
    }
    llm = llm_sequence(checklist)

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_SUNITA)),
        patch("agents.agent_followup.client", llm),
        patch("agents.agent_followup.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_followup.log_outcome", AsyncMock()),
        patch("agents.agent_followup.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        # Greeting turn
        state = await outbound_graph.ainvoke(state)
        # Patient replies
        state["messages"].append({"role": "user", "content": "Theek hoon, koi takleef nahi"})
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND FOLLOWUP — low risk, completed", result)

    mock_escalate.assert_not_called()
    assert result["call_outcome"]["status"] == "completed"
    assert result["call_outcome"]["readmission_risk"] == 0.2


@pytest.mark.asyncio
async def test_outbound_followup_high_risk_escalates_to_doctor():
    """
    Patient reports fever + pain=9 + not taking medicine.
    readmission_risk=0.8 → escalate node fires → escalate_to_doctor called.
    """
    state = outbound_state(PATIENT_SUNITA["id"], "hi-IN", "followup")

    checklist = {
        "all_answered": True,
        "reply": "Aapki halat ke baare mein doctor ko bata raha hoon.",
        "fever": True,
        "pain_level": 9,
        "medication_adherence": "no",
        "additional_concerns": "Bahut dard ho raha hai",
    }
    llm = llm_sequence(checklist)
    escalate_mock = AsyncMock()

    with (
        patch("agents.agent_followup.get_discharge_info", AsyncMock(return_value=DISCHARGE_SUNITA)),
        patch("agents.agent_followup.client", llm),
        patch("agents.agent_followup.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_followup.log_outcome", AsyncMock()),
        patch("agents.agent_followup.escalate_to_doctor", escalate_mock),
    ):
        state = await outbound_graph.ainvoke(state)
        state["messages"].append({
            "role": "user",
            "content": "Haan bukhaar hai, bahut dard hai, dawai nahi le raha"
        })
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND FOLLOWUP — high risk, escalated", result)

    escalate_mock.assert_called_once()
    escalate_args = escalate_mock.call_args
    assert escalate_args.args[0] == PATIENT_SUNITA["id"]
    assert "readmission_risk" in escalate_args.kwargs["reason"]
    assert result["call_outcome"]["status"] == "escalated"
    assert result["call_outcome"]["readmission_risk"] == 0.8


@pytest.mark.asyncio
async def test_outbound_followup_unreachable_logs_and_stops():
    """
    call_connected=False → immediately log unreachable, no checklist.
    escalate_to_doctor NOT called.
    """
    state = outbound_state(PATIENT_SUNITA["id"], "hi-IN", "followup")
    state["call_connected"] = False

    with (
        patch("agents.agent_followup.log_outcome", AsyncMock()) as mock_log,
        patch("agents.agent_followup.escalate_to_doctor", AsyncMock()) as mock_escalate,
    ):
        result = await outbound_graph.ainvoke(state)

    print_state("OUTBOUND FOLLOWUP — unreachable", result)

    mock_log.assert_called_once()
    mock_escalate.assert_not_called()
    assert result["call_outcome"]["status"] == "unreachable"
    assert result["call_outcome"]["readmission_risk"] == 0.0
