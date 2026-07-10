"""Lab report routes — GET /lab/{patient_id}, PATCH /lab/{report_id}/dispatched."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from api.database import async_session
from api.models import LabReport

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/lab/{patient_id}", summary="Get lab reports for a patient")
async def get_lab_reports(patient_id: str):
    """Get all pending and ready lab reports for a patient.
    Excludes dispatched reports — already delivered to patient.
    Called by Agent 6 (lab_status_node) via get_lab_status() tool."""
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
        return {
            "reports": [
                {
                    "report_id": str(r.report_id),
                    "test_name": r.test_name,
                    "status": r.status,
                    "ready_at": r.ready_at.isoformat() if r.ready_at else None,
                    "result_summary_en": r.result_summary_en,
                }
                for r in reports
            ]
        }


@router.patch("/lab/{report_id}/dispatched", summary="Mark a lab report as dispatched")
async def mark_dispatched(report_id: str):
    """Mark a lab report as dispatched after it has been read to the patient.
    Prevents re-reading the same result on a subsequent call."""
    async with async_session() as session:
        report = await session.get(LabReport, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        report.status = "dispatched"
        await session.commit()
        logger.info("lab: mark_dispatched report_id=%s", report_id)
        return {"status": "dispatched"}
