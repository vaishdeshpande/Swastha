# api/CLAUDE.md — FastAPI Backend + Supabase + Upstash

> **Read root CLAUDE.md first.** This file covers the FastAPI backend,
> Supabase PostgreSQL schema, Upstash Redis client setup, and seed data.
>
> FastAPI is co-located with the LiveKit Agent Worker in a single Railway
> deployment. They share the same Python process.

---

## FastAPI App Structure

### `main.py` — App factory

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from apscheduler.schedulers.asyncio import AsyncIOScheduler

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_database()        # Create tables if not exist
    await init_redis()           # Verify Upstash connection
    scheduler = AsyncIOScheduler()
    scheduler.add_job(run_outbound_jobs, "interval", minutes=30)
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown()

app = FastAPI(title="Hospital Receptionist API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
from api.routes import livekit, appointments, prescriptions, followup, analytics, doctors
app.include_router(livekit.router, prefix="/api")
app.include_router(appointments.router, prefix="/api")
app.include_router(prescriptions.router, prefix="/api")
app.include_router(followup.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")
app.include_router(doctors.router, prefix="/api")
```

**Railway Procfile:**
```
web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
```

The LiveKit agent worker runs as a background task inside the same process,
started in the `lifespan` context manager.

---

## API Routes

### POST `/api/token` — LiveKit room access

```python
# routes/livekit.py
from livekit.api import AccessToken, VideoGrants

@router.post("/token")
async def get_token(room_name: str, participant_name: str):
    """Generate a LiveKit access token for the frontend to join a room."""
    token = AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    token.identity = participant_name
    token.name = participant_name
    grant = VideoGrants(room_join=True, room=room_name)
    token.video_grants = grant
    return {"token": token.to_jwt()}
```

### GET `/api/slots/{department}/{date}` — Available appointment slots

```python
# routes/appointments.py
@router.get("/slots/{department}/{date}")
async def get_slots(department: str, date: str):
    """Returns available slots for a department on a given date.
    date format: YYYY-MM-DD"""
    slots = await db.fetch_all(
        appointments.select().where(
            appointments.c.department == department,
            appointments.c.slot_date == date,
            appointments.c.status == "open",
        )
    )
    return {"slots": [dict(s) for s in slots]}
```

### POST `/api/appointments` — Book an appointment

```python
@router.post("/appointments")
async def book_appointment(patient_id: str, slot_id: str):
    """Book a slot. Updates status from 'open' to 'booked'."""
    # 1. Verify slot is still open
    slot = await db.fetch_one(appointments.select().where(appointments.c.id == slot_id))
    if not slot or slot["status"] != "open":
        raise HTTPException(404, "Slot not available")

    # 2. Update slot
    await db.execute(
        appointments.update()
        .where(appointments.c.id == slot_id)
        .values(patient_id=patient_id, status="booked", booked_at=datetime.utcnow())
    )

    # 3. Return confirmation
    return {"status": "booked", "slot_id": slot_id, "doctor": slot["doctor_name"], "time": slot["slot_time"]}
```

### GET `/api/prescriptions/{patient_id}` — Get prescription

```python
# routes/prescriptions.py
@router.get("/prescriptions/{patient_id}")
async def get_prescriptions(patient_id: str):
    """Get most recent prescription for a patient."""
    rx = await db.fetch_one(
        prescriptions.select()
        .where(prescriptions.c.patient_id == patient_id)
        .order_by(prescriptions.c.issued_date.desc())
    )
    if not rx:
        raise HTTPException(404, "No prescription found")
    return dict(rx)
```

### POST `/api/followup/log` — Log follow-up outcome

```python
# routes/followup.py
@router.post("/followup/log")
async def log_followup(patient_id: str, outcome: dict):
    """Log post-discharge follow-up outcome."""
    await db.execute(
        discharge_followups.update()
        .where(
            discharge_followups.c.patient_id == patient_id,
            discharge_followups.c.status == "pending",
        )
        .values(outcome_json=outcome, status="completed", completed_at=datetime.utcnow())
    )
    return {"status": "logged"}
```

### GET `/api/analytics/calls` — Call analytics for /admin

```python
# routes/analytics.py
@router.get("/analytics/calls")
async def get_call_analytics(days: int = 7):
    """Aggregated call analytics for the admin dashboard."""
    since = datetime.utcnow() - timedelta(days=days)
    logs = await db.fetch_all(
        call_logs.select().where(call_logs.c.created_at >= since)
    )

    return {
        "total_calls": len(logs),
        "avg_duration_sec": mean([l["duration_sec"] for l in logs if l["duration_sec"]]),
        "language_breakdown": Counter([l["lang_code"] for l in logs]),
        "agent_activations": Counter([a for l in logs for a in (l.get("agents_used") or [])]),
        "sentiment_avg": mean([l["analytics_json"].get("sentiment_score", 0) for l in logs if l["analytics_json"]]),
        "pending_followups": await db.fetch_val(
            select(func.count()).where(discharge_followups.c.status == "pending")
        ),
        "escalations_today": len([l for l in logs if l.get("escalated")]),
    }
```

### GET `/api/doctors` — List doctors

```python
# routes/doctors.py
@router.get("/doctors")
async def list_doctors(department: Optional[str] = None):
    """List all doctors, optionally filtered by department."""
    query = doctors.select()
    if department:
        query = query.where(doctors.c.department == department)
    return {"doctors": [dict(d) for d in await db.fetch_all(query)]}
```

---

## Supabase PostgreSQL Schema

### Connection — `database.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Use asyncpg for async Supabase connection
# DATABASE_URL format: postgresql+asyncpg://postgres:password@db.xxx.supabase.co:5432/postgres
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
```

**Important:** Supabase provides a standard PostgreSQL connection string. Use `asyncpg` as the driver for async FastAPI compatibility. Replace `postgresql://` with `postgresql+asyncpg://` in the connection string.

### Tables — `models.py`

Use SQLAlchemy ORM with the following schema:

```python
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
```

### Indexes (important for performance)

The schema above already includes `index=True` on:
- `patients.phone` — Agent 2 looks up patients by phone on every call
- `appointments.department` + `appointments.slot_date` + `appointments.status` — Agent 3 queries open slots
- `discharge_followups.due_at` + `discharge_followups.status` — Cron job queries pending follow-ups
- `call_logs.call_id` — Post-call subgraph writes analytics by call ID

---

## Upstash Redis Client — `redis_client.py`

```python
from upstash_redis.asyncio import Redis

redis = Redis(
    url=UPSTASH_REDIS_REST_URL,
    token=UPSTASH_REDIS_REST_TOKEN,
)

# Verify connection on startup
async def init_redis():
    pong = await redis.ping()
    assert pong == "PONG", f"Upstash Redis connection failed: {pong}"
    print("✅ Upstash Redis connected")
```

**Important:** Use `upstash_redis.asyncio.Redis`, not the sync version. All agent tool calls are async.

---

## Seed Data — `seed.py`

Run with: `python -m api.seed`

Creates the following demo data for the live website:

### 5 Doctors
```python
doctors = [
    {"name": "Dr. Priya Sharma", "department": "general", "qualification": "MBBS, MD", "available_days": ["monday", "wednesday", "friday"]},
    {"name": "Dr. Rajesh Patel", "department": "cardiology", "qualification": "MBBS, DM Cardiology", "available_days": ["tuesday", "thursday"]},
    {"name": "Dr. Anjali Deshmukh", "department": "ortho", "qualification": "MBBS, MS Ortho", "available_days": ["monday", "thursday", "saturday"]},
    {"name": "Dr. Vikram Singh", "department": "pediatrics", "qualification": "MBBS, DCH", "available_days": ["wednesday", "friday"]},
    {"name": "Dr. Meera Joshi", "department": "dermatology", "qualification": "MBBS, MD Dermatology", "available_days": ["tuesday", "saturday"]},
]
```

### 10 Demo Patients
```python
patients = [
    {"name": "Ramesh Kumar", "phone": "+919876543210", "age": 45, "lang_pref": "hi-IN", "medical_history": [{"condition": "hypertension", "year": 2020}]},
    {"name": "Sunita Devi", "phone": "+919876543211", "age": 38, "lang_pref": "hi-IN"},
    {"name": "Arun Patil", "phone": "+919876543212", "age": 52, "lang_pref": "mr-IN", "medical_history": [{"condition": "diabetes", "year": 2018}]},
    {"name": "Priya Marathe", "phone": "+919876543213", "age": 29, "lang_pref": "mr-IN"},
    {"name": "Vijay Sharma", "phone": "+919876543214", "age": 67, "lang_pref": "hi-IN", "medical_history": [{"condition": "arthritis", "year": 2015}]},
    {"name": "Kavita Joshi", "phone": "+919876543215", "age": 41, "lang_pref": "mr-IN"},
    {"name": "Mohan Gupta", "phone": "+919876543216", "age": 55, "lang_pref": "hi-IN"},
    {"name": "Anita Bhosale", "phone": "+919876543217", "age": 33, "lang_pref": "mr-IN"},
    {"name": "Deepak Verma", "phone": "+919876543218", "age": 48, "lang_pref": "hi-IN"},
    {"name": "Sneha Kulkarni", "phone": "+919876543219", "age": 36, "lang_pref": "mr-IN"},
]
```

### 20 Appointment Slots (next 5 weekdays, 4 slots each)

```python
# Generate 4 slots per day for next 5 weekdays across departments
# Times: 09:00, 10:00, 11:00, 14:00
# Departments rotate: general, cardiology, ortho, pediatrics
# All start as status="open"
```

### 3 Prescriptions (for patients[0], patients[2], patients[4])

```python
prescriptions = [
    {
        "patient_id": patients[0]["id"],  # Ramesh Kumar
        "doctor_name": "Dr. Priya Sharma",
        "medicines": [
            {"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily morning", "duration": "30 days"},
            {"name": "Aspirin", "dosage": "75mg", "frequency": "once daily after lunch", "duration": "30 days"},
        ],
        "notes_en": "Blood pressure well controlled. Continue current medications. Follow up in 1 month. Reduce salt intake.",
        "refill_date": "2026-08-05",
    },
    # ... similar for patients[2] (diabetes) and patients[4] (arthritis)
]
```

### 2 Discharge Records (for cron/Agent 5 testing)

```python
discharge_followups = [
    {
        "patient_id": patients[1]["id"],  # Sunita Devi
        "discharge_date": "2026-07-03",
        "diagnosis": "Appendectomy - laparoscopic",
        "medications_prescribed": [{"name": "Cefixime", "dosage": "200mg", "frequency": "twice daily"}],
        "due_at": "2026-07-05T10:00:00",  # 48h post discharge — should be picked up by cron
        "status": "pending",
        "job_type": "followup",
    },
    {
        "patient_id": patients[3]["id"],  # Priya Marathe
        "discharge_date": "2026-07-04",
        "diagnosis": "Viral fever - recovered",
        "medications_prescribed": [{"name": "Paracetamol", "dosage": "500mg", "frequency": "as needed"}],
        "due_at": "2026-07-05T14:00:00",  # 24h post discharge
        "status": "pending",
        "job_type": "followup",
    },
]
```

---

## Requirements

```
# requirements.txt
fastapi>=0.110.0
uvicorn[standard]>=0.29.0
sqlalchemy[asyncio]>=2.0.0
asyncpg>=0.29.0
supabase>=2.0.0
upstash-redis>=1.0.0
livekit>=0.14.0
livekit-agents[sarvam,silero]>=0.12.0
sarvamai>=1.0.0
langgraph>=0.2.0
apscheduler>=3.10.0
python-dotenv>=1.0.0
httpx>=0.27.0
twilio>=9.0.0
pyyaml>=6.0.0
pydantic>=2.0.0
```

---

## Railway Deployment Notes

- Railway auto-detects Python from `requirements.txt`
- The `Procfile` runs `uvicorn api.main:app`
- The LiveKit agent worker starts as a background task in the FastAPI lifespan
- Set all env vars in Railway dashboard (copy from `.env`)
- SQLite is NOT used — all data is in Supabase (external)
- Redis is NOT on Railway — it's Upstash (external)
- One service, one process, one bill

---

*This file is the single source of truth for the backend. Do not contradict it in code.*