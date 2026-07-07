"""Supabase operations used by the agents. Uses the async SQLAlchemy session
from api/database.py — all calls are awaited from LangGraph agent nodes."""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from api.database import async_session
from api.models import Appointment, CallLog, DischargeFollowup, Patient, Prescription

logger = logging.getLogger(__name__)


async def get_patient_record(phone: str) -> dict | None:
    """Look up patient by phone number. Returns patient dict or None."""
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
    confirmation dict: {appointment_id, doctor_name, date, time, department}."""
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
                    "patient_id": str(patient.id),
                    "lang_code": patient.lang_pref,
                    "tts_voice": "priya" if patient.lang_pref == "hi-IN" else "kavya",
                    "job_type": followup.job_type,
                }
            )
        logger.info("db: get_due_outbound_jobs -> %d job(s) due", len(jobs))
        return jobs


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
