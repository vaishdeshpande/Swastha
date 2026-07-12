# Swastha — Multi-Agent AI Voice Receptionist for Indian Hospitals

> Built on Sarvam AI · LiveKit · LangGraph · FastAPI · Next.js

Swastha is a multi-agent voice receptionist that handles inbound patient calls in **Hindi**, **Marathi**, and code-mixed **Hinglish / Marathlish** — no IVR menus, no hold music, no English-only bots. Patients speak naturally; the system understands, acts, and responds in their language.

---

## Live Demo

**[swastha-lovat.vercel.app](https://swastha-lovat.vercel.app/)**

> **Note:** The frontend is deployed on Vercel (always live). The backend runs locally and is exposed via an ngrok tunnel due to free-tier limitations — it may not be reachable if the tunnel is down.
>
>  In case it's not running, please reach out directly. To run it yourself, clone the repo and follow the [Local Setup](#local-setup) instructions, or see [`iac/README.md`](iac/README.md).

**[Business Deck →](https://docs.google.com/presentation/d/1X_z-PBkejSe7cNxeKU6rqV2tZgx1YHCfu61MFkDo-ys/edit?slide=id.p1#slide=id.p1)**

---

## Architecture Diagram

![Swastha System Architecture](arch.png)

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

| Layer | Technology |
|---|---|
| Voice pipeline | LiveKit Agents SDK + Sarvam plugin |
| STT | Sarvam Saaras v3 |
| LLM | Sarvam sarvam-30b |
| TTS | Sarvam Bulbul v3 |
| Translation | Sarvam Mayura v1 |
| Agent orchestration | LangGraph StateGraph |
| Backend API | FastAPI + APScheduler |
| Database | Supabase PostgreSQL |
| Session cache | Upstash Redis |
| Frontend | Next.js (App Router) + LiveKit React SDK |
| Observability | LangSmith |
| Deploy — backend | ngrok tunnel (local) |
| Deploy — frontend | Vercel |

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
      └─ escalation_required → Human Handoff      — translates hand-off message, pings on-call doctor
                                      │
                               [Post-call node]   — analytics, Redis summary, schedules outbound jobs
```

### Outbound Graph (APScheduler cron, every 30 min)

```
Supabase: due_followups / pending_confirmations
      │
  Route Job
      ├─ job_type=confirmation  → [3] Scheduler outbound    — confirm tomorrow's appointment  ⚠ future scope
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
| 8 | **Event-Driven / Cron Subgraph** | Simulation →  — proactive follow-ups, not reactive responses | Action |
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

> **Full provisioning guide, deploy scripts, DB commands, and gotchas live in [`iac/README.md`](iac/README.md).**

### The fast path — one command

Requires: **Python 3.11+**, **uv**, **Node 20+**, **npm**, and a filled-in `.env`.

```bash
git clone <your-repo-url>
cd healthcareapp

cp .env.example .env          # fill in Required vars (Sarvam, LiveKit, Supabase, Redis)
./iac/run_local.sh            # provisions DB, installs deps, starts all three services
```

`run_local.sh` does everything in order — creates a venv via uv, creates the 8 Supabase tables, seeds demo data if the DB is empty, installs frontend npm deps, then starts FastAPI + LiveKit agent worker + Next.js in the background. Logs stream to `iac/.logs/`.

```
Frontend:  http://localhost:3000
Backend:   http://localhost:8000
API docs:  http://localhost:8000/docs
Admin:     http://localhost:3000/admin
```

```bash
./iac/run_local.sh --reset    # wipe + re-seed the DB, then restart
./iac/run_local.sh --stop     # take everything down
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

## Test Scenarios

Seed phone numbers run from `9876543210` through `9876543219`. Use `9876543218` for new patient registration tests.

---

### Scenario 1 — New Patient · Hindi · Appointment Booking

**Who you are:** First-time caller, no record in DB.  
**Language:** Auto-detect or Hindi

```
"Namaste, mujhe doctor se milna hai. Mere pet mein bahut dard ho raha hai."
```
When asked for details:
```
"Mera naam Arjun Mehta hai. Mera number hai 9876543218. Mujhe general doctor chahiye."
```

**Verify:** Patient registered silently (no announcement) · Hindi slots offered · slot booked · Booking card appears on screen.

---

### Scenario 2 — Existing Patient · Marathi · Appointment Booking

**Who you are:** Arun Patil (`9876543212`, Marathi preference)  
**Language:** Marathi

```
"Namaskar, mala doctor Anjali Deshmukh yanchi appointment ghyaychi ahe. Maza number 9876543212."
```

**Verify:** Registration skipped (phone found) · full response in Marathi · TTS switches to Kavya voice · ortho slot booked with Dr. Anjali Deshmukh.

---

### Scenario 3 — Prescription Query · Hindi · Doctor Notes Translation

**Who you are:** Ramesh Kumar (`9876543210`, Hindi)  
**Language:** Auto-detect

```
"Meri dawai ke baare mein poochna tha. Mera number 9876543210 hai."
```

**Verify:** Intent detected as `prescription` · Amlodipine + Aspirin fetched · English doctor notes translated to Hindi before being read aloud · no appointment booking attempted.

---

### Scenario 4 — Hinglish · Ambiguous Intent · Confidence-Gated Fanout

**Who you are:** New caller, code-mixed speech  
**Language:** Auto-detect

```
"Hi, mujhe kuch help chahiye. Mera naam Rahul hai."
```
When asked what you need:
```
"Actually doctor se milna bhi hai, aur apni medicines bhi check karni thi."
```

**Verify:** Single clarifying question covering both intents (not two separate questions) · no premature escalation · after clarifying "appointment", routes to Scheduler.

---

### Scenario 5 — Outbound Follow-up · Discharge Check

**Who you are:** Sunita Devi (`9876543211`) — discharged 24h ago, follow-up due now.  
**Trigger manually** (or wait for APScheduler) by inserting a due row into `discharge_followups`:

```sql
INSERT INTO discharge_followups (patient_id, discharge_date, diagnosis, due_at, status, job_type)
VALUES (
  (SELECT id FROM patients WHERE phone = '9876543211'),
  NOW() - INTERVAL '24 hours',
  'Hypertension',
  NOW(),
  'pending',
  'followup'
);
```

**Verify:** Agent asks about fever, pain level, medication adherence · `readmission_risk` score computed · if risk > 0.7, `escalate_to_doctor()` fires · row updated to `completed` or `escalated`.

---

> Full suite (11 scenarios) in [`TEST_SCENARIOS.md`](TEST_SCENARIOS.md).

---
