"""Billing routes — GET /billing/{patient_id}, POST /billing/{bill_id}/dispatch-link."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from api.database import async_session
from api.models import Bill
from agents.tools.notification_tools import send_sms

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/billing/{patient_id}", summary="Get outstanding bill for a patient")
async def get_patient_bill(patient_id: str):
    """Get the most recent unpaid or partial bill for a patient.
    Called by Agent 7 (billing_node) via get_bill() tool.
    Returns {bill: null} if no unpaid bills exist."""
    async with async_session() as session:
        result = await session.execute(
            select(Bill)
            .where(
                Bill.patient_id == patient_id,
                Bill.status.in_(["unpaid", "partial"]),
            )
            .order_by(Bill.created_at.desc())
            .limit(1)
        )
        bill = result.scalars().first()
        if not bill:
            return {"bill": None}
        return {
            "bill": {
                "bill_id": str(bill.bill_id),
                "amount_due": float(bill.amount_due),
                "status": bill.status,
                "items_json": bill.items_json,
                "payment_link": bill.payment_link,
            }
        }


@router.post("/billing/{bill_id}/dispatch-link", summary="Dispatch UPI payment link via SMS")
async def dispatch_billing_link(bill_id: str, phone: str):
    """Dispatch UPI payment link to patient's phone via Twilio SMS.
    Called by Agent 7 after reading bill amount to patient."""
    async with async_session() as session:
        bill = await session.get(Bill, bill_id)
        if not bill or not bill.payment_link:
            raise HTTPException(status_code=404, detail="Bill not found or no payment link available")
        message = (
            f"Pay your hospital bill of ₹{bill.amount_due:.0f} here: {bill.payment_link}"
            "\n— Hospital Receptionist"
        )
        await send_sms(phone, message)
        logger.info("billing: dispatched payment link bill_id=%s to phone=%s", bill_id, phone)
        return {"status": "dispatched"}
