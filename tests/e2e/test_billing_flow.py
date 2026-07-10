"""
E2E Scenario — Billing Flow (Agent 7)
=======================================

Graph path per ainvoke():
  language_router (no LLM) → voice_intake (LLM #1) → billing (no LLM) → post_call → END

Agent 7 is a pure-lookup node — no LLM call happens inside it.
So the ONLY LLM call per turn is voice_intake.

Turn structure:
  Turn 1: [intake_billing]  → billing node runs, amount spoken, SMS dispatched
"""

from __future__ import annotations

import pytest

from agents.graph import inbound_graph
from tests.e2e.helpers import fresh_state, run_turn, print_state
from tests.e2e.mocks import (
    graph_mocks,
    PATIENT_RAMESH,
    PATIENT_SUNITA,
    BILL_SUNITA,
    intake_billing,
    intake_unclear,
)


@pytest.mark.asyncio
async def test_hindi_billing_amount_spoken_and_sms_sent():
    """
    Patient asks "mera bill kitna hai" → intent=billing → Agent 7 runs.
    Amount spoken in Hindi, UPI link dispatched via SMS.

    1 LLM call: [intake_billing]
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.91)

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=BILL_SUNITA,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Mera bill kitna hua? Payment karna hai. Sunita, +919876543211",
        )

    print_state("BILLING — Hindi, unpaid bill", state)

    all_content = " ".join(m["content"] for m in state["messages"] if m["role"] == "assistant")
    assert state["intent"] == "billing"
    assert state["current_agent"] in ("billing", "post_call")
    assert state["escalation_required"] is False
    assert "3,200" in all_content or "3200" in all_content
    # State fields populated for frontend data channel
    assert state["bill_amount_due"] == 3200.00
    assert state["bill_sms_sent"] is True


@pytest.mark.asyncio
async def test_billing_no_outstanding_bills():
    """
    Patient has no unpaid bills → "no outstanding bills" message, no SMS.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=None,   # no unpaid bill
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Mera koi bill baki hai kya? Sunita, +919876543211",
        )

    print_state("BILLING — no outstanding bills", state)

    assert state["intent"] == "billing"
    assert state["escalation_required"] is False
    assert "no outstanding" in reply.lower() or "outstanding" in reply.lower()
    assert state["bill_amount_due"] is None


@pytest.mark.asyncio
async def test_billing_no_payment_link_skips_sms():
    """
    Bill exists but payment_link is None (pre-payment or manual payment only).
    Amount is spoken but SMS is not dispatched.
    """
    bill_no_link = {**BILL_SUNITA, "payment_link": None}
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=bill_no_link,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Bill dekhna hai, Sunita +919876543211",
        )

    print_state("BILLING — no payment link", state)

    assert state["intent"] == "billing"
    assert "3,200" in reply or "3200" in reply
    assert state["bill_sms_sent"] is False


@pytest.mark.asyncio
async def test_billing_intent_routes_not_to_scheduler():
    """
    Regression: intent=billing must NOT route to scheduler or prescription.
    appointment_id stays None.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=BILL_SUNITA,
    ):
        _, state = await run_turn(
            inbound_graph, state,
            "Bill kitna hai, Sunita +919876543211",
        )

    assert state.get("appointment_id") is None
    assert state["intent"] == "billing"


@pytest.mark.asyncio
async def test_billing_intent_unclear_first_then_resolved():
    """
    First turn: voice_intake returns intent=None → clarification question.
    Second turn: voice_intake resolves to billing → Agent 7 runs.
    The graph routes to END (await_input) after each unclear turn, so two run_turns needed.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_unclear("Aap bill dekhna chahte hain ya appointment book karna hai?")],
        patient=PATIENT_SUNITA,
        bill=BILL_SUNITA,
    ):
        _, state = await run_turn(inbound_graph, state, "Kuch poochna tha")

    assert state["intake_attempt_count"] == 1
    assert state["intent"] is None

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=BILL_SUNITA,
    ):
        _, state = await run_turn(inbound_graph, state, "Haan paisa wala")

    print_state("BILLING — unclear → resolved to billing", state)

    all_content = " ".join(m["content"] for m in state["messages"] if m["role"] == "assistant")
    assert state["intent"] == "billing"
    assert state["intake_attempt_count"] == 1
    assert "3,200" in all_content or "3200" in all_content


@pytest.mark.asyncio
async def test_marathi_billing_flow():
    """
    Marathi patient billing query — translate called with mr-IN target.
    """
    marathi_patient = {
        "id": "patient-kavita-uuid",
        "name": "Kavita Joshi",
        "phone": "+919876543215",
        "age": 41,
        "lang_pref": "mr-IN",
        "blood_group": "O-",
        "medical_history": [],
        "is_new": False,
    }
    marathi_bill = {**BILL_SUNITA, "bill_id": "bill-kavita-uuid"}
    state = fresh_state(
        detected_language="mr-IN",
        detection_confidence=0.87,
        lang_code="mr-IN",
        tts_voice="kavya",
    )

    with graph_mocks(
        llm_responses=[intake_billing(name="Kavita Joshi", phone="+919876543215")],
        patient=marathi_patient,
        bill=marathi_bill,
        detected_lang="mr-IN",
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Maza bill kiti ahe? Kavita, +919876543215",
        )

    print_state("BILLING — Marathi patient", state)

    assert state["lang_code"] == "mr-IN"
    assert state["intent"] == "billing"
    assert state["bill_amount_due"] == 3200.00


@pytest.mark.asyncio
async def test_billing_items_appear_in_reply():
    """
    Bill has 3 line items — they should appear in the spoken bill summary.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_billing()],
        patient=PATIENT_SUNITA,
        bill=BILL_SUNITA,
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Bill mein kya kya hai? Sunita +919876543211",
        )

    print_state("BILLING — line items in reply", state)

    # The bill summary includes up to 3 line items from items_json
    all_content = " ".join(m["content"] for m in state["messages"] if m["role"] == "assistant")
    assert any(
        item in all_content
        for item in ["OPD Consultation", "Blood CBC", "Medicines"]
    )


@pytest.mark.asyncio
async def test_new_patient_billing_query():
    """
    New patient calls about billing — voice_intake registers them, then Agent 7 runs.
    patient_id is populated even though the patient is new.
    """
    state = fresh_state(detected_language="hi-IN", detection_confidence=0.9)

    with graph_mocks(
        llm_responses=[intake_billing(name="Nayi Patient", phone="+919111111111")],
        patient=None,                          # not in DB
        new_patient_id="new-billing-patient",
        bill=None,                             # new patient won't have a bill
    ):
        reply, state = await run_turn(
            inbound_graph, state,
            "Bill dekhna hai, pehli baar aa raha hoon. +919111111111",
        )

    print_state("BILLING — new patient, no bill", state)

    assert state["patient_id"] == "new-billing-patient"
    assert state["is_new_patient"] is True
    assert "no outstanding" in reply.lower() or "outstanding" in reply.lower()
