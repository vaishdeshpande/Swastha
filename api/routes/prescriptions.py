from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from sqlalchemy import select

from api.database import async_session
from api.models import Prescription
from api.schemas import MedicineItem, PrescriptionResponse

router = APIRouter()


@router.get(
    "/prescriptions/{patient_id}",
    response_model=PrescriptionResponse,
    summary="Get a patient's most recent prescription",
    description="""
Returns the most recently issued prescription for `patient_id`.

`notes_en` is always in English — Agent 4 (Prescription) translates it
to the patient's language using Sarvam Mayura v1 before reading it out.

Raises `404` if no prescription exists for the patient.
""",
    responses={
        200: {"description": "Prescription found"},
        404: {"description": "No prescription on file for this patient"},
    },
)
async def get_prescriptions(
    patient_id: Annotated[str, Path(example="550e8400-e29b-41d4-a716-446655440001")],
) -> PrescriptionResponse:
    async with async_session() as session:
        result = await session.execute(
            select(Prescription)
            .where(Prescription.patient_id == patient_id)
            .order_by(Prescription.issued_date.desc())
        )
        rx = result.scalars().first()
        if not rx:
            raise HTTPException(404, "No prescription found")

        return PrescriptionResponse(
            id=str(rx.id),
            patient_id=str(rx.patient_id),
            doctor_name=rx.doctor_name,
            medicines=[MedicineItem(**m) for m in (rx.medicines or [])],
            notes_en=rx.notes_en,
            issued_date=rx.issued_date.isoformat(),
            refill_date=rx.refill_date.isoformat() if rx.refill_date else None,
        )
