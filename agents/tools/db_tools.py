"""Supabase operations used by the agents. Uses the async SQLAlchemy session
from api/database.py — all calls are awaited from LangGraph agent nodes."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from sqlalchemy import select

from api.database import async_session
from api.models import Appointment, Bill, CallLog, DischargeFollowup, LabReport, Patient, Prescription

logger = logging.getLogger(__name__)

# Spoken digit words in Hindi, Marathi, and English that STT may produce
# when a patient recites their number verbally.
_WORD_TO_DIGIT = {
    # English
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hindi transliteration (common STT outputs)
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4",
    "paanch": "5", "chhe": "6", "saat": "7", "aath": "8", "nau": "9",
    # Marathi transliteration
    "unch": "0", "eka": "1", "don": "2", "tin": "3", "char": "4",
    "panch": "5", "saha": "6", "sat": "7", "aath": "8", "nau": "9",
}


def _normalize_phone(raw: str) -> str:
    """Normalize a phone number that may have been spoken as words.

    Handles:
    - "Nine Nine Nine Nine Nine Nine Nine Nine Nine Nine" → "9999999999"
    - "नाइन नाइन नाइन..." (Hindi STT) → stripped to digits
    - "+91 98765 43210" → "9876543210" (strip country code + spaces)
    - Already-clean "9876543210" → unchanged
    """
    # Replace word-form digits (case-insensitive)
    tokens = re.split(r"[\s\-]+", raw.strip())
    digit_tokens = [_WORD_TO_DIGIT.get(t.lower(), t) for t in tokens]
    joined = "".join(digit_tokens)

    # Keep only digits
    digits_only = re.sub(r"\D", "", joined)

    # Strip leading country code: +91 or 0 prefix
    if digits_only.startswith("91") and len(digits_only) == 12:
        digits_only = digits_only[2:]
    elif digits_only.startswith("0") and len(digits_only) == 11:
        digits_only = digits_only[1:]

    # Store with +91 prefix to match seed data format
    if len(digits_only) == 10:
        return f"+91{digits_only}"

    # Return as-is if we can't clean it — DB will reject with a clear error
    logger.warning("db: could not normalize phone '%s' → '%s'", raw, digits_only)
    return digits_only


async def get_patient_record(phone: str) -> dict | None:
    """Look up patient by phone number. Returns patient dict or None."""
    phone = _normalize_phone(phone)
    async with async_session() as session:
        result = await session.execute(select(Patient).where(Patient.phone == phone))
        patient = result.scalar_one_or_none()
        if patient is None:
            logger.debug("db: no patient found for phone=%s", phone)
            return None
        logger.debug("db: found patient_id=%s for phone=%s", patient.id, phone)
        return {
            "id": str(patient.id),
            "name": patient.name,
            "phone": patient.phone,
            "age": patient.age,
            "lang_pref": patient.lang_pref,
            "blood_group": patient.blood_group,
            "medical_history": patient.medical_history,
            "is_new": patient.is_new,
        }


async def get_patient_record_by_id(patient_id: str) -> dict | None:
    """Look up patient by UUID. Returns patient dict or None."""
    async with async_session() as session:
        patient = await session.get(Patient, patient_id)
        if patient is None:
            logger.debug("db: no patient found for patient_id=%s", patient_id)
            return None
        return {
            "id": str(patient.id),
            "name": patient.name,
            "phone": patient.phone,
            "age": patient.age,
            "lang_pref": patient.lang_pref,
            "blood_group": patient.blood_group,
            "medical_history": patient.medical_history,
            "is_new": patient.is_new,
        }


async def register_patient(name: str, phone: str, age: int, lang_pref: str) -> str:
    """Create new patient in Supabase. Returns patient_id (UUID)."""
    phone = _normalize_phone(phone)
    async with async_session() as session:
        patient = Patient(name=name, phone=phone, age=age, lang_pref=lang_pref)
        session.add(patient)
        await session.commit()
        await session.refresh(patient)
        logger.info("db: registered new patient name=%s phone=%s patient_id=%s", name, phone, patient.id)
        return str(patient.id)


# ---------------------------------------------------------------------------
# Appointments — used by Agent 3 (Scheduler)
# ---------------------------------------------------------------------------

VALID_DEPARTMENTS = ["general", "cardiology", "ortho", "pediatrics", "dermatology"]

_DEPARTMENT_SYNONYMS = {
    "general": "general",
    "general physician": "general",
    "physician": "general",
    "gp": "general",
    "cardiologist": "cardiology",
    "cardiology": "cardiology",
    "heart": "cardiology",
    "orthopedic": "ortho",
    "orthopedics": "ortho",
    "ortho": "ortho",
    "bone": "ortho",
    "joint": "ortho",
    "pediatric": "pediatrics",
    "pediatrics": "pediatrics",
    "paediatrics": "pediatrics",
    "child": "pediatrics",
    "dermatologist": "dermatology",
    "dermatology": "dermatology",
    "skin": "dermatology",
}


def normalize_department(department: str) -> str:
    """Maps free-text department mentions (however Agent 2's LLM extraction
    phrased them) onto the fixed department enum the doctors/appointments
    tables actually use. The voice_intake prompt already asks the model to
    emit one of VALID_DEPARTMENTS directly — this is the safety net for
    when it doesn't."""
    key = (department or "").strip().lower()
    if key in VALID_DEPARTMENTS:
        return key
    normalized = _DEPARTMENT_SYNONYMS.get(key, key)
    if normalized != key:
        logger.debug("db: normalized department %r -> %r", key, normalized)
    return normalized


def _slot_dict(slot: Appointment) -> dict:
    return {
        "slot_id": str(slot.id),
        "doctor_name": slot.doctor_name,
        "department": slot.department,
        "date": slot.slot_date,
        "time": slot.slot_time,
    }


async def check_available_slots(department: str, date: str) -> list[dict]:
    """Query appointments table for open slots. Returns list of
    {slot_id, doctor_name, date, time, department}."""
    department = normalize_department(department)
    async with async_session() as session:
        query = select(Appointment).where(
            Appointment.department == department,
            Appointment.status == "open",
        )
        if date and date != "any":
            query = query.where(Appointment.slot_date == date)
        query = query.order_by(Appointment.slot_date, Appointment.slot_time)
        result = await session.execute(query)
        slots = [_slot_dict(s) for s in result.scalars().all()]
        logger.debug("db: check_available_slots dept=%s date=%s -> %d slot(s)", department, date, len(slots))
        return slots


async def get_next_available(department: str, n: int = 3) -> list[dict]:
    """If no slots on requested date, get next N available across all dates."""
    department = normalize_department(department)
    async with async_session() as session:
        result = await session.execute(
            select(Appointment)
            .where(Appointment.department == department, Appointment.status == "open")
            .order_by(Appointment.slot_date, Appointment.slot_time)
            .limit(n)
        )
        slots = [_slot_dict(s) for s in result.scalars().all()]
        logger.debug("db: get_next_available dept=%s n=%d -> %d slot(s)", department, n, len(slots))
        return slots


async def book_slot(patient_id: str, slot_id: str) -> dict:
    """Book an appointment. Updates slot status to 'booked'. Returns
    confirmation dict: {appointment_id, doctor_name, date, time, department}.

    Wrapped in asyncio.shield so a LangGraph task cancellation mid-query
    doesn't tear down the asyncpg connection before the commit completes."""
    async def _do_book():
        async with async_session() as session:
            slot = await session.get(Appointment, slot_id)
            if not slot or slot.status != "open":
                raise ValueError(f"Slot {slot_id} is not available")
            slot.patient_id = patient_id
            slot.status = "booked"
            slot.booked_at = datetime.utcnow()
            await session.commit()
            logger.info("db: booked slot_id=%s for patient_id=%s doctor=%s", slot_id, patient_id, slot.doctor_name)
            return {
                "appointment_id": str(slot.id),
                "doctor_name": slot.doctor_name,
                "date": slot.slot_date,
                "time": slot.slot_time,
                "department": slot.department,
            }

    return await asyncio.shield(_do_book())


async def cancel_appointment(appointment_id: str) -> bool:
    """Cancel an appointment. Updates status to 'cancelled'."""
    async with async_session() as session:
        appointment = await session.get(Appointment, appointment_id)
        if not appointment:
            logger.warning("db: cancel_appointment — appointment_id=%s not found", appointment_id)
            return False
        appointment.status = "cancelled"
        await session.commit()
        logger.info("db: cancelled appointment_id=%s", appointment_id)
        return True


async def confirm_appointment(appointment_id: str) -> bool:
    """Mark an appointment as confirmed (outbound confirmation call)."""
    async with async_session() as session:
        appointment = await session.get(Appointment, appointment_id)
        if not appointment:
            logger.warning("db: confirm_appointment — appointment_id=%s not found", appointment_id)
            return False
        appointment.confirmed = True
        await session.commit()
        logger.info("db: confirmed appointment_id=%s", appointment_id)
        return True


# ---------------------------------------------------------------------------
# Prescriptions — used by Agent 4 (Prescription)
# ---------------------------------------------------------------------------

async def get_prescription(patient_id: str) -> dict:
    """Get most recent prescription for a patient. Returns
    {medicines, notes_en, refill_date}."""
    async with async_session() as session:
        result = await session.execute(
            select(Prescription)
            .where(Prescription.patient_id == patient_id)
            .order_by(Prescription.issued_date.desc())
        )
        rx = result.scalars().first()
        if not rx:
            raise ValueError(f"No prescription found for patient {patient_id}")
        logger.debug("db: fetched prescription for patient_id=%s issued=%s", patient_id, rx.issued_date)
        return {
            "medicines": rx.medicines,
            "notes_en": rx.notes_en,
            "refill_date": rx.refill_date.isoformat() if rx.refill_date else None,
        }


async def log_query(patient_id: str, query: str, response: str) -> None:
    """Log a prescription query/response pair to call_logs for doctor review."""
    async with async_session() as session:
        session.add(
            CallLog(
                patient_id=patient_id,
                agents_used=["prescription"],
                call_outcome={"query": query, "response": response},
            )
        )
        await session.commit()
        logger.debug("db: logged prescription query for patient_id=%s", patient_id)


async def mark_reminder_sent(patient_id: str) -> None:
    """Mark a pending medication reminder job as delivered."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup).where(
                DischargeFollowup.patient_id == patient_id,
                DischargeFollowup.job_type == "rx_reminder",
                DischargeFollowup.status == "pending",
            )
        )
        job = result.scalars().first()
        if job:
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            await session.commit()
            logger.info("db: rx_reminder marked completed for patient_id=%s", patient_id)
        else:
            logger.warning("db: no pending rx_reminder job found for patient_id=%s", patient_id)


# ---------------------------------------------------------------------------
# Discharge follow-ups — used by Agent 5 (Post-Discharge Follow-up)
# ---------------------------------------------------------------------------

async def get_discharge_info(patient_id: str) -> dict:
    """Get discharge info for follow-up. Returns
    {discharge_date, diagnosis, medications}."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup)
            .where(DischargeFollowup.patient_id == patient_id, DischargeFollowup.job_type == "followup")
            .order_by(DischargeFollowup.discharge_date.desc())
        )
        discharge = result.scalars().first()
        if not discharge:
            raise ValueError(f"No discharge record found for patient {patient_id}")
        logger.debug("db: fetched discharge info for patient_id=%s (diagnosis=%s)", patient_id, discharge.diagnosis)
        return {
            "discharge_date": discharge.discharge_date.isoformat(),
            "diagnosis": discharge.diagnosis,
            "medications": discharge.medications_prescribed,
        }


async def log_outcome(patient_id: str, outcome: dict) -> None:
    """Write follow-up outcome to discharge_followups table."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup).where(
                DischargeFollowup.patient_id == patient_id,
                DischargeFollowup.job_type == "followup",
                DischargeFollowup.status == "pending",
            )
        )
        followup = result.scalars().first()
        if not followup:
            logger.warning("db: log_outcome — no pending followup found for patient_id=%s", patient_id)
            return
        followup.outcome_json = outcome
        followup.status = outcome.get("status", "completed")
        followup.completed_at = datetime.utcnow()
        await session.commit()
        logger.info("db: logged followup outcome for patient_id=%s status=%s", patient_id, followup.status)


async def get_pending_discharge(patient_id: str) -> dict | None:
    """Returns the patient's pending discharge follow-up job, if any."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup).where(
                DischargeFollowup.patient_id == patient_id,
                DischargeFollowup.job_type == "followup",
                DischargeFollowup.status == "pending",
            )
        )
        discharge = result.scalars().first()
        if not discharge:
            return None
        return {
            "id": str(discharge.id),
            "due_at": discharge.due_at.isoformat(),
            "status": discharge.status,
        }


# ---------------------------------------------------------------------------
# Outbound cron support — used by api/main.py's scheduled job runner
# ---------------------------------------------------------------------------

async def get_due_outbound_jobs() -> list[dict]:
    """Query discharge_followups for due outbound jobs. Called by cron."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup, Patient)
            .join(Patient, Patient.id == DischargeFollowup.patient_id)
            .where(DischargeFollowup.status == "pending", DischargeFollowup.due_at <= datetime.utcnow())
        )
        jobs = []
        for followup, patient in result.all():
            jobs.append(
                {
                    "job_id": str(followup.id),  # needed to mark the job done — without
                                                 # it jobs stay pending and retry forever
                    "patient_id": str(patient.id),
                    "lang_code": patient.lang_pref,
                    "tts_voice": "priya" if patient.lang_pref == "hi-IN" else "kavya",
                    "job_type": followup.job_type,
                }
            )
        logger.info("db: get_due_outbound_jobs -> %d job(s) due", len(jobs))
        return jobs


async def complete_outbound_job(job_id: str, status: str = "completed") -> None:
    """Close an outbound job so the cron stops re-processing it.
    status: 'completed' on success, 'failed' on a permanent error."""
    async with async_session() as session:
        job = await session.get(DischargeFollowup, job_id)
        if not job:
            logger.warning("db: complete_outbound_job — job_id=%s not found", job_id)
            return
        job.status = status
        job.completed_at = datetime.utcnow()
        await session.commit()
        logger.info("db: outbound job %s marked %s", job_id, status)


async def get_latest_booked_appointment(patient_id: str) -> str | None:
    """Most recent booked (not yet confirmed) appointment for a patient.
    Used by the outbound confirmation call — the confirmation job row doesn't
    store an appointment reference, so it's resolved at call time."""
    async with async_session() as session:
        result = await session.execute(
            select(Appointment.id)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.status == "booked",
            )
            .order_by(Appointment.booked_at.desc())
            .limit(1)
        )
        appt_id = result.scalar_one_or_none()
        return str(appt_id) if appt_id else None


# ---------------------------------------------------------------------------
# Lab Reports — used by Agent 6 (Lab Status)
# ---------------------------------------------------------------------------

async def get_lab_status(patient_id: str) -> list[dict]:
    """Get all pending and ready lab reports for a patient.
    Excludes 'dispatched' — already delivered to the patient."""
    async with async_session() as session:
        result = await session.execute(
            select(LabReport)
            .where(
                LabReport.patient_id == patient_id,
                LabReport.status.in_(["pending", "ready"]),
            )
            .order_by(LabReport.ordered_at.desc())
        )
        reports = result.scalars().all()
        logger.debug("db: get_lab_status patient_id=%s -> %d report(s)", patient_id, len(reports))
        return [
            {
                "report_id": str(r.report_id),
                "test_name": r.test_name,
                "status": r.status,
                "ready_at": r.ready_at.isoformat() if r.ready_at else None,
                "result_summary_en": r.result_summary_en,
            }
            for r in reports
        ]


async def mark_report_dispatched(report_id: str) -> None:
    """Flip lab_reports.status from 'ready' to 'dispatched' after reading to patient."""
    async with async_session() as session:
        report = await session.get(LabReport, report_id)
        if report:
            report.status = "dispatched"
            await session.commit()
            logger.info("db: mark_report_dispatched report_id=%s", report_id)
        else:
            logger.warning("db: mark_report_dispatched — report_id=%s not found", report_id)


# ---------------------------------------------------------------------------
# Billing — used by Agent 7 (Billing)
# ---------------------------------------------------------------------------

async def get_bill(patient_id: str) -> dict | None:
    """Get most recent unpaid or partial bill for a patient. Returns None if none exists."""
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
            logger.debug("db: get_bill patient_id=%s -> no unpaid bills", patient_id)
            return None
        logger.debug("db: get_bill patient_id=%s -> bill_id=%s amount=%.2f", patient_id, bill.bill_id, bill.amount_due)
        return {
            "bill_id": str(bill.bill_id),
            "amount_due": float(bill.amount_due),
            "status": bill.status,
            "items_json": bill.items_json,
            "payment_link": bill.payment_link,
        }


async def get_bill_by_id(bill_id: str) -> dict | None:
    """Get a specific bill by bill_id. Used by dispatch_payment_link."""
    async with async_session() as session:
        bill = await session.get(Bill, bill_id)
        if not bill:
            logger.warning("db: get_bill_by_id — bill_id=%s not found", bill_id)
            return None
        return {
            "bill_id": str(bill.bill_id),
            "amount_due": float(bill.amount_due),
            "status": bill.status,
            "items_json": bill.items_json,
            "payment_link": bill.payment_link,
        }


async def dispatch_payment_link(bill_id: str, phone: str) -> None:
    """Send UPI payment link to patient's phone via SMS. Reuses send_sms()."""
    from agents.tools.notification_tools import send_sms
    bill = await get_bill_by_id(bill_id)
    if not bill or not bill.get("payment_link"):
        logger.warning("db: dispatch_payment_link — bill_id=%s has no payment_link", bill_id)
        return
    message = (
        f"Pay your hospital bill of ₹{bill['amount_due']:.0f} here: {bill['payment_link']}"
        "\n— Hospital Receptionist"
    )
    await send_sms(phone, message)
    logger.info("db: dispatch_payment_link sent SMS to phone=%s for bill_id=%s", phone, bill_id)


async def has_pending_job(patient_id: str, job_type: str) -> bool:
    """True if the patient already has a pending outbound job of this type.
    Used by post_call to avoid scheduling duplicate confirmation/follow-up
    jobs when it runs on multiple turns of the same call."""
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup.id).where(
                DischargeFollowup.patient_id == patient_id,
                DischargeFollowup.job_type == job_type,
                DischargeFollowup.status == "pending",
            ).limit(1)
        )
        return result.scalar_one_or_none() is not None


async def schedule_outbound_job(patient_id: str, job_type: str, due_at: datetime) -> None:
    """Create a pending discharge_followups row so the cron picks it up
    at due_at. Used by the post-call subgraph to schedule confirmation
    calls and follow-ups."""
    async with async_session() as session:
        session.add(
            DischargeFollowup(
                patient_id=patient_id,
                discharge_date=datetime.utcnow(),
                due_at=due_at,
                status="pending",
                job_type=job_type,
            )
        )
        await session.commit()
        logger.info("db: scheduled outbound job type=%s for patient_id=%s due_at=%s", job_type, patient_id, due_at)
