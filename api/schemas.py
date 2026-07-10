"""Pydantic response and request schemas for all API routes.

Keeping schemas separate from routes keeps route files readable and
gives Swagger clean, fully-documented models with examples.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared / primitives
# ---------------------------------------------------------------------------

class MedicineItem(BaseModel):
    name: str = Field(..., example="Amlodipine")
    dosage: str = Field(..., example="5mg")
    frequency: str = Field(..., example="once daily morning")
    duration: str = Field(..., example="30 days")


# ---------------------------------------------------------------------------
# LiveKit token
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    room_name: str = Field(..., example="call-room-001")
    participant_name: str = Field(..., example="patient-ramesh")
    preferred_lang: str = Field("auto", example="mr-IN", description="'hi-IN', 'mr-IN', or 'auto' for STT auto-detect")

    model_config = {"json_schema_extra": {"example": {"room_name": "call-room-001", "participant_name": "patient-ramesh", "preferred_lang": "auto"}}}


class TokenResponse(BaseModel):
    token: str = Field(..., description="LiveKit JWT access token, valid for 6 hours")


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------

class SlotItem(BaseModel):
    id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    doctor_name: str = Field(..., example="Dr. Priya Sharma")
    department: str = Field(..., example="general")
    slot_date: str = Field(..., example="2026-07-10")
    slot_time: str = Field(..., example="10:00")


class SlotsResponse(BaseModel):
    slots: list[SlotItem]


class BookAppointmentRequest(BaseModel):
    patient_id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440001")
    slot_id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")

    model_config = {"json_schema_extra": {"example": {
        "patient_id": "550e8400-e29b-41d4-a716-446655440001",
        "slot_id": "550e8400-e29b-41d4-a716-446655440000",
    }}}


class BookAppointmentResponse(BaseModel):
    status: str = Field(..., example="booked")
    slot_id: str
    doctor: str = Field(..., example="Dr. Priya Sharma")
    time: str = Field(..., example="10:00")


# ---------------------------------------------------------------------------
# Prescriptions
# ---------------------------------------------------------------------------

class PrescriptionResponse(BaseModel):
    id: str
    patient_id: str
    doctor_name: str = Field(..., example="Dr. Priya Sharma")
    medicines: list[MedicineItem]
    notes_en: Optional[str] = Field(None, description="Doctor notes in English; agents translate to patient language")
    issued_date: str = Field(..., example="2026-07-01T09:00:00")
    refill_date: Optional[str] = Field(None, example="2026-08-05")


# ---------------------------------------------------------------------------
# Follow-up
# ---------------------------------------------------------------------------

class FollowupOutcomePayload(BaseModel):
    fever: bool = Field(..., example=False)
    pain_level: int = Field(..., ge=0, le=10, example=3)
    medication_adherence: str = Field(..., example="yes")
    additional_concerns: str = Field("", example="")
    readmission_risk: float = Field(..., ge=0.0, le=1.0, example=0.2)
    status: str = Field(..., example="completed")


class LogFollowupRequest(BaseModel):
    patient_id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440001")
    outcome: FollowupOutcomePayload

    model_config = {"json_schema_extra": {"example": {
        "patient_id": "550e8400-e29b-41d4-a716-446655440001",
        "outcome": {
            "fever": False,
            "pain_level": 3,
            "medication_adherence": "yes",
            "additional_concerns": "",
            "readmission_risk": 0.2,
            "status": "completed",
        },
    }}}


class LogFollowupResponse(BaseModel):
    status: str = Field("logged", example="logged")


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

class CallAnalyticsResponse(BaseModel):
    total_calls: int = Field(..., example=42)
    avg_duration_sec: float = Field(..., example=187.3)
    language_breakdown: dict[str, int] = Field(..., example={"hi-IN": 28, "mr-IN": 14})
    agent_activations: dict[str, int] = Field(
        ..., example={"language_router": 42, "voice_intake": 42, "scheduler": 30, "prescription": 12}
    )
    sentiment_avg: float = Field(..., example=0.65)
    pending_followups: int = Field(..., example=5)
    escalations_today: int = Field(..., example=2)


# ---------------------------------------------------------------------------
# Doctors
# ---------------------------------------------------------------------------

class DoctorItem(BaseModel):
    id: str
    name: str = Field(..., example="Dr. Priya Sharma")
    department: str = Field(..., example="general")
    qualification: Optional[str] = Field(None, example="MBBS, MD")
    phone: Optional[str] = Field(None, example="+919876500001")
    available_days: list[str] = Field(default_factory=list, example=["monday", "wednesday", "friday"])


class DoctorsResponse(BaseModel):
    doctors: list[DoctorItem]
