from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select

from api.database import async_session
from api.models import DischargeFollowup
from api.schemas import LogFollowupRequest, LogFollowupResponse

router = APIRouter()


@router.post(
    "/followup/log",
    response_model=LogFollowupResponse,
    summary="Log a post-discharge follow-up outcome",
    description="""
Writes the structured outcome of an Agent 5 follow-up call to the
`discharge_followups` table and marks the job as **completed**.

Called by Agent 5 (`followup_outbound_node`) via `db_tools.log_outcome()`.
Can also be called manually from the admin UI to close out a pending job.

**Outcome fields:**
- `fever` — whether the patient reported fever
- `pain_level` — 0–10 self-reported pain scale
- `medication_adherence` — `"yes"` | `"partial"` | `"no"`
- `readmission_risk` — 0.0–1.0 (≥ 0.7 triggers doctor escalation)
- `status` — `"completed"` | `"escalated"` | `"unreachable"`
""",
    responses={
        200: {"description": "Outcome logged (or no pending job found — idempotent)"},
    },
)
async def log_followup(body: LogFollowupRequest) -> LogFollowupResponse:
    async with async_session() as session:
        result = await session.execute(
            select(DischargeFollowup).where(
                DischargeFollowup.patient_id == body.patient_id,
                DischargeFollowup.status == "pending",
            )
        )
        followup = result.scalars().first()
        if followup:
            followup.outcome_json = body.outcome.model_dump()
            followup.status = "completed"
            followup.completed_at = datetime.utcnow()
            await session.commit()

    return LogFollowupResponse(status="logged")
