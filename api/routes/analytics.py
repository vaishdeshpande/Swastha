from collections import Counter
from datetime import datetime, timedelta
from statistics import mean
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from api.database import async_session
from api.models import CallLog, DischargeFollowup
from api.schemas import CallAnalyticsResponse

router = APIRouter()


@router.get(
    "/analytics/calls",
    response_model=CallAnalyticsResponse,
    summary="Aggregated call analytics",
    description="""
Returns key metrics for the `/admin` dashboard over a rolling `days` window.

**Metrics returned:**
- `total_calls` — total inbound calls in window
- `avg_duration_sec` — mean call duration
- `language_breakdown` — calls per language (`hi-IN`, `mr-IN`, …)
- `agent_activations` — how many times each LangGraph node was hit
- `sentiment_avg` — mean sentiment score from post-call Sarvam LLM analysis (-1 to 1)
- `pending_followups` — discharge follow-up jobs not yet completed
- `escalations_today` — calls that required human handoff

`days` defaults to 7. Use `days=1` for a daily snapshot, `days=30` for monthly.
""",
    responses={
        200: {"description": "Analytics summary"},
    },
)
async def get_call_analytics(
    days: Annotated[int, Query(ge=1, le=90, description="Rolling window in days", example=7)] = 7,
) -> CallAnalyticsResponse:
    since = datetime.utcnow() - timedelta(days=days)

    async with async_session() as session:
        logs_result = await session.execute(select(CallLog).where(CallLog.created_at >= since))
        logs = logs_result.scalars().all()

        pending_result = await session.execute(
            select(func.count()).select_from(DischargeFollowup).where(DischargeFollowup.status == "pending")
        )
        pending_followups = pending_result.scalar_one()

        durations = [l.duration_sec for l in logs if l.duration_sec]
        sentiments = [
            l.analytics_json.get("sentiment_score", 0) for l in logs if l.analytics_json
        ]

        return CallAnalyticsResponse(
            total_calls=len(logs),
            avg_duration_sec=mean(durations) if durations else 0.0,
            language_breakdown=dict(Counter(l.lang_code for l in logs if l.lang_code)),
            agent_activations=dict(Counter(a for l in logs for a in (l.agents_used or []))),
            sentiment_avg=mean(sentiments) if sentiments else 0.0,
            pending_followups=pending_followups,
            escalations_today=len([l for l in logs if l.escalated]),
        )
