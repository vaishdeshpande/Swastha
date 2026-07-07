from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query
from sqlalchemy import select

from api.database import async_session
from api.models import Appointment
from api.schemas import BookAppointmentRequest, BookAppointmentResponse, SlotItem, SlotsResponse

router = APIRouter()


@router.get(
    "/slots/{department}/{date}",
    response_model=SlotsResponse,
    summary="List open appointment slots",
    description="""
Returns all **open** (unbooked) appointment slots for a department on a given date.

`department` must be one of: `general`, `cardiology`, `ortho`, `pediatrics`, `dermatology`

`date` must be in `YYYY-MM-DD` format.

Agent 3 (Scheduler) calls this directly via `db_tools.check_available_slots()`.
This endpoint is also useful for the admin UI and testing.
""",
    responses={
        200: {"description": "List of open slots (may be empty)"},
    },
)
async def get_slots(
    department: Annotated[str, Path(example="general")],
    date: Annotated[str, Path(example="2026-07-10")],
) -> SlotsResponse:
    async with async_session() as session:
        result = await session.execute(
            select(Appointment).where(
                Appointment.department == department,
                Appointment.slot_date == date,
                Appointment.status == "open",
            )
        )
        slots = result.scalars().all()
        return SlotsResponse(
            slots=[
                SlotItem(
                    id=str(s.id),
                    doctor_name=s.doctor_name,
                    department=s.department,
                    slot_date=s.slot_date,
                    slot_time=s.slot_time,
                )
                for s in slots
            ]
        )


@router.post(
    "/appointments",
    response_model=BookAppointmentResponse,
    summary="Book an appointment slot",
    description="""
Marks an **open** slot as **booked** for a given patient.

Raises `404` if the slot doesn't exist or has already been taken.

Agent 3 (Scheduler) calls this via `db_tools.book_slot()` after the patient confirms a slot.
""",
    responses={
        200: {"description": "Slot booked successfully"},
        404: {"description": "Slot not found or already booked"},
    },
)
async def book_appointment(body: BookAppointmentRequest) -> BookAppointmentResponse:
    async with async_session() as session:
        slot = await session.get(Appointment, body.slot_id)
        if not slot or slot.status != "open":
            raise HTTPException(404, "Slot not available")

        slot.patient_id = body.patient_id
        slot.status = "booked"
        slot.booked_at = datetime.utcnow()
        await session.commit()

        return BookAppointmentResponse(
            status="booked",
            slot_id=str(slot.id),
            doctor=slot.doctor_name,
            time=slot.slot_time,
        )
