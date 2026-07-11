# Swastha — Multi-Agent AI Voice Receptionist for Indian Hospitals

> Built on Sarvam AI · LiveKit · LangGraph · FastAPI · Next.js

Swastha is a multi-agent voice receptionist that handles inbound patient calls in **Hindi**, **Marathi**, and code-mixed **Hinglish / Marathlish** — no IVR menus, no hold music, no English-only bots. Patients speak naturally; the system understands, acts, and responds in their language.

---

## Architecture Diagram

<!-- INSERT ARCHITECTURE DIAGRAM HERE -->
<!-- Recommended: Excalidraw or draw.io export showing the voice pipeline, LangGraph inbound/outbound graphs, API layer, and three-layer memory (RAM → Redis → Supabase) -->

---

## What It Does

| Patient says | System does |
|---|---|
| "मुझे डॉक्टर से मिलना है" | Detects Hindi · collects name + phone · books appointment · confirms in Hindi |
| "माझ्या औषधांबद्दल माहिती द्या" | Detects Marathi · fetches prescription · translates doctor notes → reads aloud |
| "Meri lab report ka kya hua?" | Handles Hinglish · looks up lab report status · dispatches result over SMS |
| "मेरा बिल कितना है?" | Fetches outstanding bill · reads amount · sends UPI payment link via SMS |
| Call ends | Runs post-call analytics (sentiment, resolution, talk-time) · schedules 24h/72h follow-up outbound calls |

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Voice pipeline | LiveKit Agents SDK + Sarvam plugin | WebRTC audio bridge |
| STT | Sarvam Saaras v3 | `language=unknown` for auto-detect |
| LLM | Sarvam sarvam-30b | 1.94s TTFT — fastest for voice latency |
| TTS | Sarvam Bulbul v3 | 37 native Indic voices (hi-IN, mr-IN) |
| Translation | Sarvam Mayura v1 | Doctor notes EN → hi-IN / mr-IN |
| Agent orchestration | LangGraph StateGraph | Inbound + outbound graphs, conditional routing |
| Backend API | FastAPI + APScheduler | Single process: API + Agent Worker + Cron |
| Database | Supabase PostgreSQL | 8 tables, free forever |
| Session cache | Upstash Redis | 3 key patterns, TTL-based, free forever |
| Frontend | Next.js (App Router) + LiveKit React SDK | Patient voice UI + admin dashboard |
| Observability | LangSmith | Full LangGraph trace per call |
| Deploy — backend | Railway | `$5/mo` credit covers the demo |
| Deploy — frontend | Vercel | Zero-config GitHub deploy |

---

## Seven-Agent Architecture

### Inbound Graph (per patient utterance)

```
Patient speaks
      │
  [1] Language Router      — detects hi-IN / mr-IN / en-IN, 2-turn hysteresis before switching
      │
  [2] Voice Intake         — extracts name, phone, intent, department; registers new patients
      │
      ├─ intent=book        → [3] Scheduler       — checks slots, books appointment, sends WhatsApp
      ├─ intent=prescription → [4] Prescription   — fetches medicines, translates notes, reads aloud
      ├─ intent=lab         → [6] Lab Status      — looks up report, dispatches via SMS
      ├─ intent=billing     → [7] Billing         — reads bill amount, sends UPI payment link
      └─ escalation_required → Human Handoff      — translates hand-off message, pings Slack
                                      │
                               [Post-call node]   — analytics, Redis summary, schedules outbound jobs
```

### Outbound Graph (APScheduler cron, every 30 min)

```
Supabase: due_followups / pending_confirmations
      │
  Route Job
      ├─ job_type=confirmation  → [3] Scheduler outbound    — confirm tomorrow's appointment
      ├─ job_type=rx_reminder   → [4] Prescription outbound — remind patient to take medicines
      └─ job_type=followup      → [5] Follow-up             — post-discharge check (fever, pain, meds)
                                          │
                                     readmission_risk > 0.7 → Escalate (alert on-call doctor)
```

---

## Agentic Design Patterns

Nine distinct patterns are implemented across the system.

| # | Pattern | Where it lives | Category |
|---|---|---|---|
| 1 | **Router / Dispatcher** | `route_after_intake()` fans intent → specialist agent | Orchestration |
| 2 | **Sequential Pipeline** | Agent 1 → Agent 2 always runs unconditionally before any branching | Orchestration |
| 3 | **Human-in-the-Loop** | Any agent sets `escalation_required=True` → `human_handoff_node` → Slack alert | Reliability |
| 4 | **Stateful Multi-Turn** | `intake_collected` accumulates fields across turns — the LLM never re-asks for info the patient already gave | Memory |
| 5 | **Multi-Layer Memory** | RAM (`AgentState`) → Upstash Redis (TTL 7–90d) → Supabase (permanent) | Memory |
| 6 | **Tool-Calling / ReAct** | Agents 3, 4, 5 — LLM decides which tool to call, calls it, observes result, replies | Action |
| 7 | **Guardrails** | Input: emergency detection, STT confidence gate · Output: language consistency check, medical boundary enforcement, TTS length cap | Reliability |
| 8 | **Event-Driven / Cron Subgraph** | APScheduler → outbound LangGraph every 30 min — proactive follow-ups, not reactive responses | Action |
| 9 | **Post-Processing Subgraph** | `post_call_node` — batch STT analytics + Redis summary + outbound job scheduling fires after the patient hangs up | Action |

**Key design notes:**
- Patterns 1 + 2 explain the graph shape: fixed sequential pipeline first, conditional router second.
- Pattern 4 (`intake_collected`) is the most important UX pattern — without it, patients repeat themselves every turn.
- Pattern 6 applies to Agents 3/4/5 only. Agents 1, 6, 7 call one predetermined function each — no LLM tool-choice needed.
- Pattern 7 guardrails run at the LiveKit session layer, outside the graph — they fire even if the graph crashes.
- Pattern 8 is what makes Swastha proactive. Most voice agents are purely reactive.

---

## Three-Layer Memory

| Layer | Storage | Scope | Key patterns |
|---|---|---|---|
| In-flight RAM | LangGraph `AgentState` | One call | Full TypedDict per turn |
| Short-term | Upstash Redis | 7–90 days | `recent_calls:{id}` · `session:{call_id}` · `lang_pref:{id}` |
| Long-term | Supabase PostgreSQL | Forever | 8 tables (patients, doctors, appointments, prescriptions, discharge_followups, call_logs, lab_reports, bills) |

---

## Four Latency Optimisations

| # | Name | What it does | Saving |
|---|---|---|---|
| 1 | Parallel background registration | Fires `get_patient_record(phone)` via `asyncio.create_task` the moment the phone number is extracted from the LLM stream | Hides DB round-trip behind LLM generation |
| 2 | Slot pre-fetch on intent detection | Caches available slots in Redis the moment `intent=book` is confirmed, before the scheduler node runs | 200–400ms per booking turn |
| 3 | Streaming partial state extraction | Runs phone-number regex on the partial LLM stream — fires DB lookup before the LLM finishes its sentence | 400–800ms intake → scheduler handoff |
| 4 | Confidence-gated multi-agent fanout | On ambiguous utterances, two lightweight classifiers run in parallel; routes directly if one scores ≥ 0.65, asks one combined clarifying question if both do | Prevents false escalations on ambiguous inputs |

---

## Language Support

Configured in `config/languages.yaml` — adding a new language requires **no code changes**:

```yaml
languages:
  hi-IN:
    name: Hindi
    tts_voice: priya
    tts_model: bulbul:v3
    greeting: "नमस्ते! मैं आपकी कैसे मदद कर सकती हूँ?"
    enabled: true

  mr-IN:
    name: Marathi
    tts_voice: kavya
    tts_model: bulbul:v3
    greeting: "नमस्कार! मी तुम्हाला कशी मदत करू शकते?"
    enabled: true

  # Phase 2 — add with zero code changes:
  # kn-IN, ta-IN, te-IN, bn-IN, gu-IN ...
```

---

## Prerequisites

### Accounts & API Keys

| Service | What you need | Free tier |
|---|---|---|
| [Sarvam AI](https://www.sarvam.ai/) | `SARVAM_API_KEY` | ₹1,000 free credits |
| [LiveKit Cloud](https://livekit.io/) | `LIVEKIT_URL` · `LIVEKIT_API_KEY` · `LIVEKIT_API_SECRET` | 50,000 min/month free |
| [Supabase](https://supabase.com/) | `SUPABASE_URL` · `SUPABASE_ANON_KEY` · `SUPABASE_SERVICE_ROLE_KEY` · `DATABASE_URL` | 500MB free forever |
| [Upstash Redis](https://upstash.com/) | `UPSTASH_REDIS_REST_URL` · `UPSTASH_REDIS_REST_TOKEN` | 500K commands/month free |
| [LangSmith](https://smith.langchain.com/) | `LANGCHAIN_API_KEY` | Free developer tier |

### Local Tools

- **Python 3.11+**
- **Node.js 20+** and **npm**
- **Git**

---

## Local Setup

### 1. Clone and create the virtual environment

```bash
git clone <your-repo-url>
cd healthcareapp

python3.11 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Fill in all values — see the Prerequisites table above
```

### 3. Provision the database

```bash
# Create all 8 tables in Supabase
python iac/db_setup.py

# Seed with 10 patients, 5 doctors, open slots, lab reports, bills
python api/seed.py
```

### 4. Pre-generate greeting audio (optional but recommended)

Pre-baking the TTS greeting eliminates cold-start latency on the first call.

```bash
python scripts/generate_greetings.py
python scripts/generate_fallback_wavs.py
```

### 5. Start the backend

```bash
# Terminal 1 — FastAPI + APScheduler cron
uvicorn api.main:app --reload --port 8000

# Terminal 2 — LiveKit Agent Worker
python voice/livekit_agent.py dev
```

API: `http://localhost:8000` · Swagger docs: `http://localhost:8000/docs`

### 6. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`.

### 7. Run the tests

```bash
# From project root with .venv active
pytest tests/ -v
```

---

## Environment Variables Reference

```bash
# Sarvam AI
SARVAM_API_KEY=

# LiveKit
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
DATABASE_URL=postgresql://postgres:password@db.your-project.supabase.co:5432/postgres

# Upstash Redis
UPSTASH_REDIS_REST_URL=https://your-db.upstash.io
UPSTASH_REDIS_REST_TOKEN=

# LangSmith (set LANGCHAIN_TRACING_V2=false to disable)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=hospital-receptionist

# App
FRONTEND_URL=https://your-app.vercel.app
PORT=8000
LOG_LEVEL=INFO
```

---

## Deployment

### Backend — Railway

```bash
# Set all env vars in the Railway dashboard, then:
railway up
# One service = FastAPI + Agent Worker (single Python process)
```

### Frontend — Vercel

```bash
cd frontend
vercel deploy
# Set NEXT_PUBLIC_BACKEND_URL to your Railway URL in the Vercel dashboard
```

**Total monthly cost: ~$5** (Railway starter credit; all other services are free tier).

---

## API Reference

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/token` | Issue a LiveKit room token for a patient session |
| `GET` | `/api/slots` | Available appointment slots (filter by department / date) |
| `POST` | `/api/appointments` | Book a slot |
| `GET` | `/api/prescriptions/{patient_id}` | Patient's prescriptions |
| `GET` | `/api/doctors` | List all doctors |
| `GET` | `/api/lab/{patient_id}` | Lab report statuses |
| `PATCH` | `/api/lab/{report_id}/dispatched` | Mark a report as dispatched |
| `GET` | `/api/billing/{patient_id}` | Outstanding bills |
| `POST` | `/api/billing/{bill_id}/dispatch-link` | Send UPI payment link via SMS |
| `POST` | `/api/followup/log` | Log an outbound follow-up outcome |
| `GET` | `/api/analytics/calls` | Aggregated call analytics for the admin dashboard |
| `GET` | `/health` | Railway health check |

Full interactive docs: `/docs` (Swagger) · `/redoc`

---

## Frontend Pages

| Route | Description |
|---|---|
| `/` | Patient voice UI — mic button, language selector (Hindi / Marathi / Auto), live transcript, agent activity feed, and result cards for bookings, lab reports, and bills |
| `/admin` | Admin dashboard — call count, language breakdown, sentiment trend, agent activation heatmap, follow-up queue |

---

## Post-Call Analytics

After every call the `post_call` LangGraph node runs asynchronously:

1. Batch STT on the call recording (Sarvam Saaras)
2. sarvam-30b analysis → `sentiment_score`, `issue_resolved`, `agent_talk_time_pct`, `patient_talk_time_pct`, `key_topics`
3. PII-scrubbed summary written to Redis `recent_calls:{patient_id}` (TTL 7 days) — Agent 2 reads this so the patient never re-explains on their next call
4. Full analytics JSON persisted to `call_logs` in Supabase
5. If patient was recently discharged → schedules 24h / 72h outbound follow-up jobs

---

## Known Issues

See [`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) for the current bug list.

Feature gaps not yet implemented:
- Appointment reminder calls (outbound, pre-visit)
- Prescription refill reminders (outbound, ongoing medication)

---

## Business Context

- India has **1.5M+ clinics and hospitals**
- Front-desk staff costs **₹12,000–18,000 / month**
- **80% of patient queries** are repeatable: appointments, prescriptions, directions, reports
- Unit economics: 500 calls/month × ₹2/call = **₹1,000/month vs ₹15,000/month** for a human receptionist
- ROI payback: **Day 1** — 24/7 availability, 11 Indic languages, zero hold time

---

*Built for the Sarvam AI Pre-Sales Take-Home — production-grade, no mocks, no placeholder webhooks.*
