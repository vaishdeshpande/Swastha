from typing import Annotated, Optional

from fastapi import APIRouter, Query
from sqlalchemy import select

from api.database import async_session
from api.models import Doctor
from api.schemas import DoctorItem, DoctorsResponse

router = APIRouter()


@router.get(
    "/doctors",
    response_model=DoctorsResponse,
    summary="List doctors",
    description="""
Returns all doctors, optionally filtered by `department`.

Valid departments: `general`, `cardiology`, `ortho`, `pediatrics`, `dermatology`

Used by the frontend to populate the department selector and by Agent 3 (Scheduler)
to validate department names during booking.
""",
    responses={
        200: {"description": "List of doctors (may be empty for an unknown department)"},
    },
)
async def list_doctors(
    department: Annotated[
        Optional[str],
        Query(description="Filter by department", example="cardiology"),
    ] = None,
) -> DoctorsResponse:
    async with async_session() as session:
        query = select(Doctor)
        if department:
            query = query.where(Doctor.department == department)
        result = await session.execute(query)
        doctors = result.scalars().all()

        return DoctorsResponse(
            doctors=[
                DoctorItem(
                    id=str(d.id),
                    name=d.name,
                    department=d.department,
                    qualification=d.qualification,
                    phone=d.phone,
                    available_days=d.available_days or [],
                )
                for d in doctors
            ]
        )
