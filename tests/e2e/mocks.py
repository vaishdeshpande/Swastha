"""Reusable mock factories for e2e tests.

HOW THE GRAPH CALLS THE LLM PER ainvoke():
============================================

inbound_graph always runs: language_router → voice_intake → (specialist agent) → post_call

Within ONE ainvoke() call:
  - voice_intake can loop back to itself if intent=None (up to MAX_INTAKE_ATTEMPTS=3 times).
  - scheduler or prescription is called once after voice_intake resolves.
  - lab_status and billing are pure-lookup (no LLM) — voice_intake is the only LLM call.

So the number of LLM calls per ainvoke():
  intent resolved on 1st try  → 2 LLM calls: [intake_book, scheduler_action]
  intent unclear once, then resolved → 3 LLM calls: [intake_none, intake_book, scheduler_action]
  intent unclear all 3 times (escalate) → 3 LLM calls: [intake_none, intake_none, intake_none]
  prescription query, intent 1st try → 2 LLM calls: [intake_rx, rx_decision]
  lab / billing intent, 1st try → 1 LLM call: [intake_lab | intake_billing]  ← no specialist LLM

Pass ALL expected LLM responses for the entire ainvoke() in the right order.
"""

from __future__ import annotations

import json
from contextlib import contextmanager, ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# Preset data
# ---------------------------------------------------------------------------

PATIENT_RAMESH = {
    "id": "patient-ramesh-uuid",
    "name": "Ramesh Kumar",
    "phone": "+919876543210",
    "age": 45,
    "lang_pref": "hi-IN",
    "blood_group": "O+",
    "medical_history": [{"condition": "hypertension", "year": 2020}],
    "is_new": False,
}

PATIENT_SUNITA = {
    "id": "patient-sunita-uuid",
    "name": "Sunita Devi",
    "phone": "+919876543211",
    "age": 38,
    "lang_pref": "hi-IN",
    "blood_group": "A+",
    "medical_history": [],
    "is_new": False,
}

OPEN_SLOTS_GENERAL = [
    {"slot_id": "slot-g1", "doctor_name": "Dr. Priya Sharma", "department": "general", "date": "2026-07-10", "time": "10:00"},
    {"slot_id": "slot-g2", "doctor_name": "Dr. Priya Sharma", "department": "general", "date": "2026-07-10", "time": "11:00"},
    {"slot_id": "slot-g3", "doctor_name": "Dr. Priya Sharma", "department": "general", "date": "2026-07-11", "time": "09:00"},
]

OPEN_SLOTS_CARDIOLOGY = [
    {"slot_id": "slot-c1", "doctor_name": "Dr. Rajesh Patel", "department": "cardiology", "date": "2026-07-11", "time": "14:00"},
]

BOOKING_CONFIRMATION = {
    "appointment_id": "appt-uuid-001",
    "doctor_name": "Dr. Priya Sharma",
    "date": "2026-07-10",
    "time": "10:00",
    "department": "general",
}

PRESCRIPTION_RAMESH = {
    "medicines": [
        {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily morning", "duration": "30 days"},
        {"name": "Aspirin", "dosage": "75mg", "frequency": "once daily after lunch", "duration": "30 days"},
    ],
    "notes_en": "Blood pressure well controlled. Continue current medications. Reduce salt intake.",
    "refill_date": "2026-08-05",
}

LAB_REPORTS_RAMESH = [
    {
        "report_id": "report-cbc-uuid",
        "test_name": "Complete Blood Count (CBC)",
        "status": "ready",
        "ready_at": "2026-07-06T14:00:00",
        "result_summary_en": "Hemoglobin is slightly low at 10.8 g/dL. All other values within normal range.",
    },
]

LAB_REPORTS_PENDING = [
    {
        "report_id": "report-lipid-uuid",
        "test_name": "Lipid Panel",
        "status": "pending",
        "ready_at": None,
        "result_summary_en": None,
    },
]

LAB_REPORTS_MIXED = LAB_REPORTS_RAMESH + LAB_REPORTS_PENDING

BILL_SUNITA = {
    "bill_id": "bill-sunita-uuid",
    "amount_due": 3200.00,
    "status": "unpaid",
    "items_json": [
        {"desc": "OPD Consultation", "qty": 1, "amount": 500},
        {"desc": "Blood CBC Test", "qty": 1, "amount": 700},
        {"desc": "Medicines", "qty": 1, "amount": 2000},
    ],
    "payment_link": "upi://pay?pa=hospital@okaxis&am=3200&cu=INR&tn=HospitalBill",
}

DISCHARGE_SUNITA = {
    "discharge_date": "2026-07-04T00:00:00",
    "diagnosis": "Appendectomy - laparoscopic",
    "medications": [{"name": "Cefixime", "dosage": "200mg", "frequency": "twice daily"}],
}


# ---------------------------------------------------------------------------
# Canned LLM responses — import these in tests for readability
# ---------------------------------------------------------------------------

def intake_book(name="Ramesh Kumar", phone="+919876543210", dept="general") -> dict:
    return {
        "intent": "book",
        "patient_name": name,
        "phone": phone,
        "department": dept,
        "urgency": "normal",
        "reply": None,
    }

def intake_prescription(name="Ramesh Kumar", phone="+919876543210") -> dict:
    return {
        "intent": "prescription",
        "patient_name": name,
        "phone": phone,
        "department": "general",
        "urgency": "normal",
        "reply": None,
    }

def intake_unclear(reply="Kripya apna naam aur zaroorat batayein") -> dict:
    return {"intent": None, "reply": reply}

def sched_check_slots(date="any") -> dict:
    return {"action": "check_slots", "date": date, "distress": False}

def sched_confirm(slot_id="slot-g1") -> dict:
    return {"action": "confirm_booking", "chosen_slot_id": slot_id, "distress": False}

def sched_cancel(appt_id="appt-uuid-001") -> dict:
    return {"action": "cancel", "cancel_appointment_id": appt_id, "distress": False}

def sched_clarify(reply="Kaunsi date aapko chahiye?") -> dict:
    return {"action": "clarify", "reply": reply, "distress": False}

def intake_lab(name="Ramesh Kumar", phone="+919876543210") -> dict:
    return {
        "intent": "lab",
        "patient_name": name,
        "phone": phone,
        "department": None,
        "urgency": "normal",
        "reply": None,
    }

def intake_billing(name="Sunita Devi", phone="+919876543211") -> dict:
    return {
        "intent": "billing",
        "patient_name": name,
        "phone": phone,
        "department": None,
        "urgency": "normal",
        "reply": None,
    }

def rx_answer(reply="Aapko Amlodipine subah leni hai.") -> dict:
    return {"reply": reply, "escalate": False}

def rx_escalate(reply="Doctor se milein.") -> dict:
    return {"reply": reply, "escalate": True}


# ---------------------------------------------------------------------------
# LLM mock builder
# ---------------------------------------------------------------------------

def llm_sequence(*responses: dict | str) -> MagicMock:
    """Build a MagicMock that returns each response in sequence.
    Pass ALL responses expected for the ENTIRE ainvoke() call, in order.

    Each returned object supports both:
    - non-streaming: response.choices[0].message.content
    - streaming: async for chunk in response  (one chunk with the full content)
    """
    mock_client = MagicMock()
    side_effects = []
    for resp in responses:
        content = json.dumps(resp) if isinstance(resp, dict) else resp

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

        side_effects.append(_StreamableResponse(content))

    mock_client.chat.completions.side_effect = side_effects
    return mock_client


# ---------------------------------------------------------------------------
# Main context manager
# ---------------------------------------------------------------------------

@contextmanager
def graph_mocks(
    *,
    llm_responses: list[dict | str] = (),
    # DB
    patient: dict | None = PATIENT_RAMESH,
    new_patient_id: str = "new-patient-uuid",
    slots: list[dict] = (),
    booking: dict = BOOKING_CONFIRMATION,
    prescription: dict | None = PRESCRIPTION_RAMESH,
    discharge: dict | None = None,
    lab_reports: list[dict] = (),
    bill: dict | None = None,
    # Redis
    cached_lang: str | None = None,
    # Language detection
    detected_lang: str = "hi-IN",
):
    """Patch all external I/O for one complete ainvoke() call.

    IMPORTANT: llm_responses must list ALL LLM calls that will happen
    within the ainvoke(), in order. See module docstring for call counts.
    """
    llm_mock = llm_sequence(*llm_responses)

    _lang_configs = {
        "hi-IN": {"tts_voice": "priya", "tts_model": "bulbul:v3", "name": "Hindi", "enabled": True},
        "mr-IN": {"tts_voice": "kavya", "tts_model": "bulbul:v3", "name": "Marathi", "enabled": True},
    }

    # prescription mock changes based on whether prescription is None
    if prescription is not None:
        _rx_mock = AsyncMock(return_value=prescription)
    else:
        _rx_mock = AsyncMock(side_effect=ValueError("no prescription"))

    if discharge is not None:
        _discharge_mock = AsyncMock(return_value=discharge)
    else:
        _discharge_mock = AsyncMock(side_effect=ValueError("no discharge"))

    patches = [
        # ── Sarvam LLM (shared mock across all agent modules) ──
        patch("agents.agent_voice_intake.client", llm_mock),
        patch("agents.agent_scheduler.client", llm_mock),
        patch("agents.agent_prescription.client", llm_mock),
        patch("agents.agent_followup.client", llm_mock),

        # ── Translate — passthrough so replies stay readable ──
        patch("agents.agent_scheduler.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_prescription.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.agent_followup.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
        patch("agents.graph.translate_text", AsyncMock(side_effect=lambda t, **_: t)),

        # ── Language ID ──
        patch("agents.agent_language_router.sarvam_identify_language",
              AsyncMock(return_value=detected_lang)),
        patch("agents.agent_language_router.redis_get", AsyncMock(return_value=cached_lang)),
        patch("agents.agent_language_router.load_language_config",
              lambda lc: _lang_configs.get(lc, _lang_configs["hi-IN"])),

        # ── Redis (stub entire client) ──
        patch("agents.tools.redis_tools.redis", MagicMock(
            get=AsyncMock(return_value=None),
            set=AsyncMock(),
            lpush=AsyncMock(),
            ltrim=AsyncMock(),
            expire=AsyncMock(),
            lrange=AsyncMock(return_value=[]),
        )),

        # ── DB: patient ──
        patch("agents.agent_voice_intake.get_patient_record", AsyncMock(return_value=patient)),
        patch("agents.agent_voice_intake.register_patient", AsyncMock(return_value=new_patient_id)),

        # ── DB: appointments ──
        patch("agents.agent_scheduler.check_available_slots", AsyncMock(return_value=list(slots))),
        patch("agents.agent_scheduler.get_next_available", AsyncMock(return_value=list(slots)[:3])),
        patch("agents.agent_scheduler.book_slot", AsyncMock(return_value=booking)),
        patch("agents.agent_scheduler.cancel_appointment", AsyncMock(return_value=True)),
        patch("agents.agent_scheduler.confirm_appointment", AsyncMock(return_value=True)),

        # ── DB: prescription ──
        patch("agents.agent_prescription.get_prescription", _rx_mock),
        patch("agents.agent_prescription.log_query", AsyncMock()),
        patch("agents.agent_prescription.mark_reminder_sent", AsyncMock()),

        # ── DB: followup ──
        patch("agents.agent_followup.get_discharge_info", _discharge_mock),
        patch("agents.agent_followup.log_outcome", AsyncMock()),

        # ── DB: lab reports (Agent 6) ──
        patch("agents.agent_lab_status.get_lab_status", AsyncMock(return_value=list(lab_reports))),
        patch("agents.agent_lab_status.mark_report_dispatched", AsyncMock()),
        patch("agents.agent_lab_status.translate_text", AsyncMock(side_effect=lambda t, **_: t)),

        # ── DB: billing (Agent 7) ──
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=bill)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=patient)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()),
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),

        # ── Intent classifier — stub to return all-zero scores (below threshold) ──
        # The new fanout uses a single call returning 6 scores. All zeros → fanout
        # returns (None, {all 0.0}, None) → normal clarification loop takes over.
        patch("agents.tools.intent_classifier.client", MagicMock(
            chat=MagicMock(completions=MagicMock(return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content=(
                    '{"book": 0.0, "prescription": 0.0, "lab": 0.0,'
                    ' "billing": 0.0, "followup": 0.0, "query": 0.0}'
                )))]
            )))
        )),

        # ── Notifications ──
        patch("agents.graph.escalate_to_doctor", AsyncMock()),
        patch("agents.agent_followup.escalate_to_doctor", AsyncMock()),

        # ── Post-call analytics — patch the DB/Redis calls inside post_call_node
        #    since the node is already compiled into the graph and can't be replaced.
        patch("analytics.call_analytics.save_call_summary", AsyncMock()),
        patch("analytics.call_analytics.save_lang_preference", AsyncMock()),
        patch("analytics.call_analytics.save_call_log", AsyncMock()),
        patch("analytics.call_analytics.schedule_outbound_job", AsyncMock()),
        patch("analytics.call_analytics.get_pending_discharge", AsyncMock(return_value=None)),
        patch("analytics.call_analytics.sarvam_batch_stt", AsyncMock(return_value="")),
        patch("analytics.call_analytics.sarvam_analyze_call", AsyncMock(return_value={})),
    ]

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        yield
