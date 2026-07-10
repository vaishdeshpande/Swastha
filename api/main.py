import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()  # MUST run before any module-level os.environ[...] reads below

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from agents.graph import outbound_graph
from agents.tools.db_tools import get_due_outbound_jobs
from api.database import init_database
from api.redis_client import init_redis

logger = logging.getLogger("api.main")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")

scheduler = AsyncIOScheduler()


async def run_outbound_jobs():
    """Query due_followups and pending_confirmations from Supabase.
    For each due job, invoke the outbound LangGraph with the right job_type.
    One job failing (bad data, transient API error) must not block the
    rest of the batch — the retry loop lives in the underlying job status,
    not here."""
    due_jobs = await get_due_outbound_jobs()
    for job in due_jobs:
        initial_state = {
            "patient_id": job["patient_id"],
            "lang_code": job["lang_code"],
            "tts_voice": job["tts_voice"],
            "job_type": job["job_type"],
            "messages": [],
            "current_agent": "route_job",
            "escalation_required": False,
        }
        try:
            await outbound_graph.ainvoke(initial_state)
        except Exception:
            logger.exception("Outbound job failed for patient %s (job_type=%s)", job["patient_id"], job["job_type"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_database()
    await init_redis()
    scheduler.add_job(run_outbound_jobs, "interval", minutes=30)
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown()


app = FastAPI(
    title="Hospital Receptionist API",
    version="1.0.0",
    description="""
## Sarvam AI — Multi-Agent Voice Receptionist for Indian Hospitals

This API backs the LiveKit-powered voice receptionist that handles inbound patient calls
in **Hindi**, **Marathi**, and **Hinglish/Marathlish** (code-mixed speech).

### What it does
- Issues LiveKit room tokens for the patient-facing voice UI
- Exposes appointment, prescription, and follow-up data consumed by the LangGraph agents
- Serves aggregated call analytics for the `/admin` dashboard

### Auth
All endpoints are open for the demo. In production, add an `Authorization: Bearer` header
backed by Supabase JWT verification.

### Voice pipeline
The voice pipeline is **not** in this API — it runs as a LiveKit Agent Worker in the same
process (see `voice/livekit_agent.py`). The agents communicate with this API via
`agents/tools/db_tools.py` (SQLAlchemy async) and `agents/tools/redis_tools.py` (Upstash).

### Docs
- **Swagger UI**: `/docs`
- **ReDoc**: `/redoc`
- **OpenAPI JSON**: `/openapi.json`
""",
    contact={
        "name": "Sarvam AI — Hospital Receptionist",
        "email": "vaishnavi.deshpande2105@gmail.com",
    },
    license_info={"name": "Private"},
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from api.routes import livekit, appointments, prescriptions, followup, analytics, doctors, lab, billing

app.include_router(livekit.router, prefix="/api", tags=["Voice / LiveKit"])
app.include_router(appointments.router, prefix="/api", tags=["Appointments"])
app.include_router(prescriptions.router, prefix="/api", tags=["Prescriptions"])
app.include_router(followup.router, prefix="/api", tags=["Follow-up"])
app.include_router(analytics.router, prefix="/api", tags=["Analytics"])
app.include_router(doctors.router, prefix="/api", tags=["Doctors"])
app.include_router(lab.router, prefix="/api", tags=["Lab Reports"])
app.include_router(billing.router, prefix="/api", tags=["Billing"])


@app.get("/health", tags=["Health"], summary="Health check")
async def health():
    """Returns `{"status": "ok"}`. Used by Railway health checks and uptime monitors."""
    return {"status": "ok"}
