# CLAUDE.md — Hospital / Clinic Receptionist Voice Agent

> **Purpose of this file:** This is the root context file for Claude Code.
> Read it fully before touching any code. Then read the sub-CLAUDE.md files
> in `agents/`, `api/`, and `frontend/` for implementation details.
>
> **Assignment:** Sarvam AI Pre-Sales Take-Home — build a production-grade,
> multi-agent AI voice receptionist for Indian hospitals.

---

## What This Project Does

Patients call in (or open the website) and speak in Hindi, Marathi, or
code-mixed Hinglish/Marathlish. The system:

1. Detects their language automatically
2. Registers them silently if they're new
3. Routes to the right agent based on intent
4. Books appointments, answers prescription queries, checks lab reports, reads bills, or follows up post-discharge
5. Confirms in the patient's language via TTS
6. Runs post-call analytics (sentiment, resolution, talk-time)
7. Triggers outbound follow-up calls at 24h/72h via a cron-driven LangGraph subgraph

Everything is real code — no mocks, no n8n, no placeholder webhooks.

---

## Stack Decisions (Final — Do Not Change)

| Layer | Technology | Why This, Not That |
|---|---|---|
| Voice pipeline | LiveKit Agents SDK + Sarvam plugin | Official Sarvam integration, ~40 lines to bootstrap |
| STT | Sarvam Saaras v3 | Auto-detects Hindi/Marathi/Hinglish with `language=unknown` |
| LLM | Sarvam sarvam-30b | 164 tok/s, 1.94s TTFT — fastest for voice latency |
| TTS | Sarvam Bulbul v3 | 37 native Indic voices, hi-IN + mr-IN |
| Translation | Sarvam Mayura v1 | Doctor notes EN→hi-IN/mr-IN |
| Agent orchestration | LangGraph (stateful graph) | Explicit conditional edges, clean multi-agent routing |
| Backend API | FastAPI (Python) | Co-located with LiveKit agent worker in one process |
| Long-term DB | Supabase PostgreSQL | Free forever, 500MB, REST + realtime built in |
| Short-term memory | Upstash Redis | Free forever, 500K cmds/month, TTL-based expiry |
| Frontend | Next.js (App Router) | LiveKit React SDK works natively |
| Frontend deploy | Vercel (free) | Zero-config GitHub deploy |
| Backend deploy | Railway ($5/mo credit) | One Python process: FastAPI + Agent Worker |
| Dev tooling | Sarvam MCP (uvx sarvam-mcp) | Test Sarvam APIs from Claude Code terminal |

**Why NOT these alternatives:**
- NOT n8n: All automation is LangGraph subgraphs — no external workflow tools
- NOT sarvam-105b: 2.06s TTFT is too slow for voice (30b is 1.94s)
- NOT Railway PostgreSQL: $1/mo after trial, barely runs one service — Supabase is free forever
- NOT CrewAI: Task-oriented, not built for real-time streaming voice
- NOT Pipecat: LiveKit is what Sarvam's own docs recommend and integrate with

---

## Repository Structure

```
hospital-receptionist/
├── CLAUDE.md                         # ← YOU ARE HERE
├── README.md
├── .env.example
├── requirements.txt
├── docker-compose.yml                # optional local full-stack
├── Procfile                          # Railway: web: uvicorn api.main:app + agent worker
│
├── agents/                           # LangGraph multi-agent system
│   ├── CLAUDE.md                     # ← agent-specific implementation guide
│   ├── __init__.py
│   ├── graph.py                      # LangGraph StateGraph (inbound + outbound)
│   ├── state.py                      # Shared AgentState TypedDict
│   ├── agent_language_router.py      # Agent 1
│   ├── agent_voice_intake.py         # Agent 2
│   ├── agent_scheduler.py            # Agent 3 (inbound + outbound)
│   ├── agent_prescription.py         # Agent 4 (inbound + outbound)
│   ├── agent_followup.py             # Agent 5 (outbound only)
│   ├── agent_lab_status.py           # Agent 6 — inbound, lab report lookup
│   ├── agent_billing.py              # Agent 7 — inbound, bill + UPI dispatch
│   ├── prompts/                      # System prompts per agent per language
│   │   ├── language_router.py
│   │   ├── voice_intake.py
│   │   ├── scheduler.py
│   │   ├── prescription.py
│   │   └── followup.py
│   └── tools/
│       ├── db_tools.py               # get_patient, check_slots, book_slot, get_lab_status, get_bill, etc.
│       ├── redis_tools.py            # Upstash Redis read/write helpers + slot_cache pre-fetch
│       ├── translate_tools.py        # Sarvam Translate API wrappers
│       ├── notification_tools.py     # Slack webhook, SMS via Twilio, dispatch_payment_link
│       ├── intent_classifier.py      # Confidence-gated parallel fanout (Scenario 4 optimization)
│       └── language_config.py        # Load tts_voice/greeting from languages.yaml
│
├── voice/
│   ├── CLAUDE.md                     # (optional, small — can reference agents/CLAUDE.md)
│   └── livekit_agent.py              # LiveKit AgentSession entrypoint — bridges LiveKit ↔ LangGraph
│
├── api/                              # FastAPI backend
│   ├── CLAUDE.md                     # ← API-specific implementation guide
│   ├── main.py                       # App factory, CORS, router registration
│   ├── routes/
│   │   ├── livekit.py                # POST /token
│   │   ├── appointments.py           # GET /slots, POST /appointments
│   │   ├── prescriptions.py          # GET /prescriptions/{patient_id}
│   │   ├── followup.py               # POST /followup/log
│   │   ├── analytics.py              # GET /analytics/calls
│   │   ├── doctors.py                # GET /doctors
│   │   ├── lab.py                    # GET /lab/{patient_id}, PATCH /lab/{report_id}/dispatched
│   │   └── billing.py                # GET /billing/{patient_id}, POST /billing/{bill_id}/dispatch-link
│   ├── models.py                     # SQLAlchemy ORM models (8 tables)
│   ├── database.py                   # Supabase connection via SQLAlchemy
│   ├── redis_client.py               # Upstash Redis client setup
│   └── seed.py                       # Seed script: 10 patients, 5 doctors, 20 slots, etc.
│
├── analytics/
│   └── call_analytics.py             # Post-call: Sarvam STT Batch + sarvam-30b analysis
│
├── frontend/                         # Next.js App Router
│   ├── CLAUDE.md                     # ← frontend-specific implementation guide
│   ├── app/
│   │   ├── page.tsx                  # Main patient-facing voice UI
│   │   ├── admin/page.tsx            # Admin dashboard
│   │   └── api/
│   │       └── token/route.ts        # Proxy to FastAPI POST /token
│   ├── components/
│   │   ├── VoiceButton.tsx           # Mic button + LiveKit room join
│   │   ├── TranscriptPanel.tsx       # Live STT transcript display
│   │   ├── AgentActivityFeed.tsx     # Which agent is active (WebSocket)
│   │   ├── LanguageSelector.tsx      # Hindi | Marathi | Auto-detect
│   │   └── AdminDashboard.tsx        # Charts, call logs, sentiment
│   └── lib/
│       └── livekit.ts                # LiveKit React hooks setup
│
└── config/
    └── languages.yaml                # Language config — add new langs here only
```

---

## Language Config (Extensible Design)

`config/languages.yaml` — adding a new language requires ONLY editing this file:

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

  # Phase 2 — add any of these with zero code changes:
  # kn-IN: { name: Kannada, tts_voice: roopa, tts_model: bulbul:v3, greeting: "...", enabled: false }
  # ta-IN: { name: Tamil, tts_voice: kavitha, tts_model: bulbul:v3, greeting: "...", enabled: false }
  # te-IN: { name: Telugu, tts_voice: ..., tts_model: bulbul:v3, greeting: "...", enabled: false }
```

All agents read from this file. When you add a language, agents automatically:
- Set the TTS voice from `tts_voice`
- Use the greeting from `greeting`
- Pass `lang_code` to Sarvam Translate

---

## 3-Layer Memory Architecture

### Layer 1 — In-flight (RAM)
- **What:** LangGraph `AgentState` TypedDict
- **Scope:** One call only. Dies when the call ends.
- **Storage:** LangGraph MemorySaver checkpointer (in-memory)
- **Contains:** messages, lang_code, tts_voice, patient_id, intent, department, urgency, current_agent, escalation_required, call_outcome
- **Cost:** Free, zero infra

### Layer 2 — Short-term (Upstash Redis)
- **What:** Cross-call context per patient
- **Scope:** Recent history, survives call end
- **Storage:** Upstash Redis (free forever, 500K cmds/month, 256MB)
- **Keys:**
  - `recent_calls:{patient_id}` — last 5 call summaries, TTL 7 days. Agent 2 reads this so the patient never re-explains.
  - `session:{call_id}` — active call state snapshot, TTL 30 min. Safety net if LiveKit drops mid-call.
  - `lang_pref:{patient_id}` — patient's preferred language, TTL 90 days. Agent 1 reads this and skips detection on repeat calls.
- **Free tier math:** 100 calls/day × 10 cmds = 30K/month. Free limit is 500K. Usage: 6%. Headroom: 16x.
- **Cost:** Free forever

### Layer 3 — Long-term (Supabase PostgreSQL)
- **What:** Permanent business data
- **Scope:** Lives forever
- **Storage:** Supabase PostgreSQL (free forever, 500MB, no credit card)
- **Tables:** patients, doctors, appointments, prescriptions, discharge_followups, call_logs
- **Why Supabase not Railway:** Railway after trial gives $1/mo — barely runs one service with no DB room. Supabase is genuinely free with REST + realtime built in.
- **Cost:** Free forever

---

## Infrastructure — What Runs Where

| Service | Platform | Cost | What It Does |
|---|---|---|---|
| Frontend (Next.js) | Vercel | Free | Patient voice UI + /admin dashboard |
| Backend (FastAPI + Agent Worker) | Railway | $5/mo credit | One Python process — API routes + LiveKit agent loop |
| PostgreSQL | Supabase | Free forever | All long-term data (8 tables — 6 original + lab_reports + bills) |
| Redis | Upstash | Free forever | Short-term session + lang memory (3 key patterns) |
| Voice infra | LiveKit Cloud | Free (50K min/mo) | WebRTC room + audio bridge |
| AI APIs | Sarvam | Free (₹1K credits) | STT + LLM + TTS + Translate |
| Dev tooling | Sarvam MCP | Free | Test APIs from Claude Desktop / Claude Code |

**Total monthly cost for demo: $5**

---

## Environment Variables (`.env.example`)

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

# Twilio (for outbound SMS/WhatsApp)
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_PHONE_NUMBER=

# Slack (for escalation webhook)
SLACK_WEBHOOK_URL=

# LangSmith (LangGraph auto-tracing — set LANGCHAIN_TRACING_V2=false to disable)
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=
LANGCHAIN_PROJECT=hospital-receptionist

# App
FRONTEND_URL=https://your-app.vercel.app
PORT=8000
LOG_LEVEL=INFO
```

---

## Build Order (Day-by-Day)

### Saturday — API Spike
1. Create Sarvam account → get API key
2. Create LiveKit Cloud account → get URL + keys
3. Create Supabase project → get connection string
4. Create Upstash Redis DB → get REST URL + token
5. Run Sarvam LiveKit hello-world (40 lines from docs)
6. Test STT→TTS pipeline end-to-end in console mode
7. Verify Hindi and Marathi audio round-trip works

### Sunday — Core Voice Pipeline
1. Implement `state.py` and `graph.py` skeleton (inbound graph only)
2. Build Agent 1 (Language Router) — test with Hindi and Marathi utterances
3. Build Agent 2 (Voice Intake) — tool calls to Supabase
4. Build `livekit_agent.py` connecting to LangGraph
5. End-to-end: speak Hindi → LangGraph routes → response in Hindi

### Monday — All 5 Agents + Backend
1. Build Agent 3 (Scheduler) + check_slots / book_slot tools
2. Build Agent 4 (Prescription) + get_prescription tools
3. Build Agent 5 (Follow-up) skeleton + outbound graph
4. FastAPI backend: all routes, Supabase schema via SQLAlchemy, seed data
5. Upstash Redis: implement 3 key patterns with TTLs
6. Post-call LangGraph subgraph: Batch STT + analytics

### Tuesday — Frontend + Deploy
1. Next.js: VoiceButton, TranscriptPanel, AgentActivityFeed
2. Language selector wired to LiveKit room config
3. Deploy backend to Railway (one process: FastAPI + agent)
4. Deploy frontend to Vercel
5. Verify full flow on live URL
6. Record Loom demo (4 min)

### Wednesday AM — Polish
1. Admin dashboard (/admin page)
2. Call analytics pipeline (async post-call)
3. README with architecture diagram + Excalidraw link
4. GitHub cleanup: remove secrets, add .env.example, tag v1.0
5. Business write-up (1.5 pages)

---

## Demo Script (for Loom recording)

**Scene 1 — Hindi Appointment Booking (90s)**
Open website → select "Auto-detect" → click mic → speak:
"नमस्ते, मुझे डॉक्टर से मिलना है। मेरे पेट में दर्द है।"
Show agent activity: Language Router → Voice Intake → Scheduler.
System books slot, confirms in Hindi. WhatsApp sent.

**Scene 2 — Marathi Prescription Query (60s)**
New call → speak Marathi:
"माझ्या औषधांबद्दल मला माहिती हवी आहे"
System fetches prescription, translates doctor notes to Marathi, reads out.

**Scene 3 — Language switch mid-call (30s)**
Start in Hindi, switch to Marathi mid-sentence.
Show system adapts voice and language in real-time.

**Scene 4 — Admin Dashboard (30s)**
Show /admin: call count, language breakdown, agent activations, follow-up queue.

**Scene 5 — Business ROI (30s)**
1-slide: ₹15,000/month receptionist vs ₹2/call AI. 24/7. 11 languages.

---

## Business Context (for write-up)

- India has 1.5M+ clinics and hospitals
- Front-desk staff costs ₹12,000–18,000/month
- 80% of patient queries are repeatable (appointments, prescriptions, directions)
- Sarvam advantage: code-mixing (Hinglish/Marathlish), native Indic voices, India-hosted GPUs, INR pricing
- Unit economics: 500 calls/month × ₹2/call = ₹1,000/month vs ₹15,000/month receptionist
- ROI payback: Day 1. 24/7 availability included.

---

## Sarvam MCP Setup (Dev Tooling)

Add to `~/.claude.json` (Claude Desktop global config):

```json
{
  "mcpServers": {
    "sarvam": {
      "command": "uvx",
      "args": ["sarvam-mcp"],
      "env": {
        "SARVAM_API_KEY": "your_key_here"
      }
    }
  }
}
```

Use in Claude Code to test:
- TTS: `sarvam_tools_tts_stream` with speaker=priya, model=bulbul:v3
- STT: `sarvam_tools_stt_transcribe` on a .wav file
- Translate: `sarvam_tools_translate` from en-IN to hi-IN
- Check quota: `sarvam_tools_recall`

---

## Architectural Optimizations (Implemented)

Four latency/quality improvements implemented across `agents/agent_voice_intake.py`,
`agents/agent_scheduler.py`, `agents/tools/intent_classifier.py`, and `agents/tools/redis_tools.py`.

### Scenario 1 — Parallel Background Registration
When phone is extracted from the LLM stream, `asyncio.create_task` immediately fires
`get_patient_record(phone)`. An optimistic UUID (`str(uuid.uuid4())`) is reserved and written
to `state["optimistic_patient_id"]` while the task runs. `voice_intake_node` awaits the
task result at the end — by then it is almost always already done. If the patient is new,
`register_patient` is called before the scheduler needs to write the appointment.
Zero latency added to the patient conversation.

### Scenario 2 — Slot Pre-fetch on Intent Detection
The moment `intent == "book"` is confirmed at the end of `voice_intake_node` (and `department`
is known), a background `asyncio.create_task` calls `check_available_slots` and writes the
result to `slot_cache:{department}:{date}` in Redis (TTL 5 min). `scheduler_node` reads
from this cache first; it only falls back to a live Supabase query on cache miss. Saves
200–400ms mid-conversation Supabase round-trip.

### Scenario 3 — Streaming Partial State Extraction
`_extract_patient_info` uses `stream=True` on the sarvam-30b call. As tokens arrive,
`_try_extract_phone(accumulated)` runs a regex against the partial JSON. The moment a
complete 10-digit phone number is parseable in the stream, `asyncio.create_task(get_patient_record(phone))`
fires — before the LLM has finished its sentence. Cuts 400–800ms from intake → scheduler
handoff because phone identification and LLM generation overlap instead of sequencing.
Falls back to non-streaming if the SDK doesn't support `stream=True`.

### Scenario 4 — Confidence-Gated Multi-Agent Fanout
When the first LLM pass returns `intent=None` (ambiguous utterance), two lightweight
sarvam-30b classifiers run in parallel via `asyncio.gather` — one biased toward "book",
one toward "prescription". Decision logic:
- One score ≥ 0.65, other < 0.65 → route directly to that intent (no clarifying question)
- Both ≥ 0.65 → synthesize one clarifying question covering both: "book new appointment, or check medicines?"
- Both < 0.65 → fall through to normal clarification loop
Prevents the escalation_required fallback triggering too early on genuinely ambiguous inputs.
Implementation lives in `agents/tools/intent_classifier.py`.

---

## Do Not

- Do NOT use `vad=` parameter in AgentSession — Sarvam handles VAD internally
- Do NOT hardcode `language="hi-IN"` in STT — always use `language="unknown"` for auto-detect
- Do NOT use sarvam-105b for voice — too slow (2.06s TTFT vs 1.94s for 30b)
- Do NOT mock anything — real n8n-free code, real webhooks, real DB
- Do NOT store secrets in code — use `.env` always
- Do NOT use localStorage in React — use React state
- Do NOT deploy DB on Railway — use Supabase (free forever)
- Do NOT deploy Redis on Railway — use Upstash (free forever)
- Do NOT create separate Railway services — one process runs both FastAPI + Agent Worker

---

## Sub-CLAUDE.md Files

Read these before implementing their respective directories:

- **`agents/CLAUDE.md`** — LangGraph graphs (inbound + outbound), all 7 agents (5 original + Lab Status + Billing), AgentState, tools, system prompts, conditional routing logic, 9 agentic design patterns, observability
- **`api/CLAUDE.md`** — FastAPI routes, Supabase schema (8 tables), Upstash Redis patterns, seed data
- **`frontend/CLAUDE.md`** — Next.js App Router, LiveKit React SDK, neomorphic design system, talking voice assistant, admin dashboard, Vercel config

---

*Last updated: Architecture finalized. Build can begin.*