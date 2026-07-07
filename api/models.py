from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON, Float, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base
import uuid
from datetime import datetime

Base = declarative_base()


class Patient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    phone = Column(String(15), unique=True, nullable=False, index=True)  # Lookup key for Agent 2
    age = Column(Integer)
    lang_pref = Column(String(10), default="hi-IN")            # "hi-IN" | "mr-IN"
    blood_group = Column(String(5))
    medical_history = Column(JSON, default=list)                # [{condition, year, notes}]
    is_new = Column(Boolean, default=True)                      # Set to False after first completed call
    created_at = Column(DateTime, default=datetime.utcnow)


class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    department = Column(String(100), nullable=False, index=True)  # "cardiology", "general", "ortho"
    qualification = Column(String(200))
    phone = Column(String(15))
    available_days = Column(JSON, default=list)                    # ["monday", "wednesday", "friday"]


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=True)  # Null = open slot
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    doctor_name = Column(String(200))                              # Denormalized for fast reads
    department = Column(String(100), nullable=False, index=True)
    slot_date = Column(String(10), nullable=False, index=True)     # "2026-07-08"
    slot_time = Column(String(10), nullable=False)                 # "10:00"
    status = Column(String(20), default="open", index=True)        # "open" | "booked" | "cancelled" | "completed"
    confirmed = Column(Boolean, default=False)                     # Set by outbound confirmation call
    booked_at = Column(DateTime)


class Prescription(Base):
    __tablename__ = "prescriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id = Column(UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False)
    doctor_name = Column(String(200))
    medicines = Column(JSON, nullable=False)                       # [{"name": "Paracetamol", "dosage": "500mg", "frequency": "twice daily", "duration": "5 days"}]
    notes_en = Column(Text)                                        # Doctor's notes in English — Agent 4 translates this
    issued_date = Column(DateTime, default=datetime.utcnow)
    refill_date = Column(DateTime)


class DischargeFollowup(Base):
    __tablename__ = "discharge_followups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    discharge_date = Column(DateTime, nullable=False)
    diagnosis = Column(String(500))
    medications_prescribed = Column(JSON)
    due_at = Column(DateTime, nullable=False, index=True)          # When to call (24h or 72h post discharge)
    status = Column(String(20), default="pending", index=True)     # "pending" | "completed" | "escalated" | "unreachable"
    outcome_json = Column(JSON)                                    # {fever, pain_level, medication_adherence, readmission_risk}
    completed_at = Column(DateTime)
    job_type = Column(String(20), default="followup")              # "followup" | "confirmation" | "rx_reminder"


class CallLog(Base):
    __tablename__ = "call_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"))
    call_id = Column(String(100), index=True)                      # LiveKit room ID
    lang_code = Column(String(10))
    recording_path = Column(Text)
    analytics_json = Column(JSON)                                  # {sentiment_score, issue_resolved, talk_times, key_topics}
    duration_sec = Column(Integer)
    call_outcome = Column(JSON)
    agents_used = Column(JSON)                                     # ["language_router", "voice_intake", "scheduler"]
    escalated = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
