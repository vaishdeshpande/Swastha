"""Tests for Agent 7 — Billing."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import make_state

UNPAID_BILL = {
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

PATIENT_WITH_PHONE = {
    "id": "patient-uuid-001",
    "name": "Sunita Devi",
    "phone": "+919876543211",
    "age": 38,
    "lang_pref": "hi-IN",
    "blood_group": "A+",
    "medical_history": [],
    "is_new": False,
}


@pytest.mark.asyncio
async def test_bill_amount_spoken_and_link_dispatched():
    """Happy path: unpaid bill → amount spoken, SMS dispatched, link_msg appended."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=UNPAID_BILL)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()) as mock_dispatch,
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    assert result["current_agent"] == "billing"
    assert result["escalation_required"] is False
    # Two assistant messages: bill amount + SMS confirmation
    assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 2
    assert "3,200" in assistant_msgs[0]["content"] or "3200" in assistant_msgs[0]["content"]
    assert "payment link" in assistant_msgs[1]["content"].lower() or "sent" in assistant_msgs[1]["content"].lower()
    mock_dispatch.assert_called_once_with("bill-sunita-uuid", "+919876543211")


@pytest.mark.asyncio
async def test_bill_amount_stored_in_state():
    """bill_amount_due and bill_sms_sent are written to state after billing_node runs."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=UNPAID_BILL)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()),
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    assert result["bill_amount_due"] == 3200.00
    assert result["bill_sms_sent"] is True


@pytest.mark.asyncio
async def test_no_bill_returns_no_outstanding_message():
    """get_bill returns None → 'no outstanding bills' message, no SMS dispatch."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=None)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()) as mock_dispatch,
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    last_msg = result["messages"][-1]["content"]
    assert "no outstanding" in last_msg.lower() or "outstanding bills" in last_msg.lower()
    mock_dispatch.assert_not_called()
    assert result["bill_amount_due"] is None


@pytest.mark.asyncio
async def test_bill_without_payment_link_skips_sms():
    """Bill exists but payment_link is None → amount spoken, no SMS dispatched."""
    bill_no_link = {**UNPAID_BILL, "payment_link": None}
    state = make_state(messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=bill_no_link)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()) as mock_dispatch,
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    # Amount message but no SMS confirmation
    assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
    assert len(assistant_msgs) == 1
    mock_dispatch.assert_not_called()
    assert result["bill_sms_sent"] is False


@pytest.mark.asyncio
async def test_items_included_in_bill_summary():
    """Top 3 line items are included in the bill summary spoken to the patient."""
    state = make_state(messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=UNPAID_BILL)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()),
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    first_msg = [m for m in result["messages"] if m["role"] == "assistant"][0]["content"]
    assert "OPD Consultation" in first_msg or "Blood CBC" in first_msg or "Medicines" in first_msg


@pytest.mark.asyncio
async def test_missing_patient_id_returns_error_message():
    """No patient_id in state → error message without calling get_bill."""
    state = make_state(patient_id=None, messages=[])

    with (
        patch("agents.agent_billing.get_bill", AsyncMock()) as mock_get,
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    mock_get.assert_not_called()
    last_msg = result["messages"][-1]["content"]
    assert "unable" in last_msg.lower() or "contact" in last_msg.lower()


@pytest.mark.asyncio
async def test_marathi_patient_translate_called_with_correct_lang():
    """translate_text is called with target_lang=mr-IN for Marathi patient."""
    state = make_state(lang_code="mr-IN", tts_voice="kavya", messages=[])
    translate_calls: list[tuple] = []

    async def capture_translate(text, source_lang="en-IN", target_lang="hi-IN"):
        translate_calls.append((text, target_lang))
        return text

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=UNPAID_BILL)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=PATIENT_WITH_PHONE)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()),
        patch("agents.agent_billing.translate_text", capture_translate),
    ):
        from agents.agent_billing import billing_node
        await billing_node(state)

    assert any(lang == "mr-IN" for _, lang in translate_calls)


@pytest.mark.asyncio
async def test_patient_record_missing_phone_skips_sms():
    """Patient has no phone number in record → SMS not dispatched, no crash."""
    state = make_state(messages=[])
    patient_no_phone = {**PATIENT_WITH_PHONE, "phone": None}

    with (
        patch("agents.agent_billing.get_bill", AsyncMock(return_value=UNPAID_BILL)),
        patch("agents.agent_billing.get_patient_record_by_id", AsyncMock(return_value=patient_no_phone)),
        patch("agents.agent_billing.dispatch_payment_link", AsyncMock()) as mock_dispatch,
        patch("agents.agent_billing.translate_text", AsyncMock(side_effect=lambda t, **_: t)),
    ):
        from agents.agent_billing import billing_node
        result = await billing_node(state)

    mock_dispatch.assert_not_called()
    assert result["bill_sms_sent"] is False
