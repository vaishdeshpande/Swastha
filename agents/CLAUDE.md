# agents/CLAUDE.md — LangGraph Multi-Agent System

> **Read root CLAUDE.md first.** This file covers the LangGraph implementation:
> both graphs (inbound + outbound), all 5 agents, shared state, tools, and
> the LiveKit integration layer.

---

## Architecture Overview

Two separate LangGraph `StateGraph` instances:

1. **Inbound Graph** — triggered when a patient calls in or opens the website.
   Flow: START → Agent 1 → Agent 2 → intent? → Agent 3 or 4 → escalate? → Post-Call Subgraph → END
2. **Outbound Graph** — triggered by APScheduler cron every 30 minutes.
   Flow: Cron → job_type? → Agent 3/4/5 outbound → risk? → Escalate → END

Both graphs share the same `AgentState` TypedDict and the same tool functions.

---

## Shared State — `state.py`

```python
from typing import TypedDict, Optional, List, Literal

class AgentState(TypedDict):
    # ── Session identity ──
    session_id: str                         # Stable UUID for the full conversation.
                                            # Generated once at session start (CLI / LiveKit
                                            # entrypoint). Never changes, even on LiveKit
                                            # reconnect. Used as the Redis session key instead
                                            # of call_id, which is the LiveKit room ID and may
                                            # change on reconnect.

    # ── Set by Agent 1 (Language Router) ──
    lang_code: str                          # "hi-IN" | "mr-IN" | "en-IN"
    tts_voice: str                          # "priya" | "kavya" | etc. from languages.yaml
    tts_model: str                          # "bulbul:v3"
    detected_language: Optional[str]        # Raw language hint from STT metadata
    detection_confidence: Optional[float]   # STT confidence for detected_language

    # ── Set by Agent 2 (Voice Intake) ──
    patient_id: Optional[str]               # Supabase UUID or None
    patient_name: Optional[str]
    is_new_patient: bool                    # True if registered during this call
    intent: Optional[Literal["book", "prescription", "followup", "query"]]
    department: Optional[str]               # "cardiology", "general", "ortho", etc.
    urgency: Literal["normal", "urgent"]
    intake_attempt_count: int               # Clarification loop counter (max 3)
    intake_collected: dict                  # Persistent partial extraction across turns.
                                            # Keys: intent, phone, patient_name, age,
                                            #       department, urgency.
                                            # Each field is set once and never cleared.
                                            # voice_intake merges new LLM extractions in
                                            # every turn, then injects already_collected
                                            # into the system prompt so the LLM never
                                            # re-asks for fields already provided.

    # ── Conversation ──
    messages: List[dict]                    # Full conversation history [{role, content}]
    current_agent: str                      # "language_router" | "voice_intake" | "scheduler" | etc.
                                            # Frontend reads this for Agent Activity Feed

    # ── Escalation ──
    escalation_required: bool
    escalation_reason: Optional[str]

    # ── Post-call ──
    call_id: Optional[str]                  # LiveKit room ID — may differ from session_id
    call_recording_path: Optional[str]
    call_outcome: Optional[dict]            # Written by post-call subgraph
    call_start_time: Optional[str]          # ISO timestamp

    # ── Set by Agent 3 (Scheduler) ──
    offered_slots: Optional[List[dict]]     # Slots most recently read out to the patient
    appointment_id: Optional[str]           # Appointment being booked/cancelled/confirmed

    # ── Outbound graph only ──
    job_type: Optional[Literal["confirmation", "rx_reminder", "followup"]]
    call_connected: Optional[bool]          # False if the outbound call went to voicemail / no answer
```

**Rules for state mutations:**
- Each agent ONLY writes to its own section. Agent 1 never touches `patient_id`. Agent 3 never touches `lang_code`.
- `messages` is append-only. Never delete or modify earlier messages.
- `current_agent` is updated at the START of each agent node (first line of the function). This is what the frontend reads via WebSocket.

---

## Inbound Graph — `graph.py`

### Node definitions

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

graph.add_node("language_router", language_router_node)
graph.add_node("voice_intake", voice_intake_node)
graph.add_node("scheduler", scheduler_node)
graph.add_node("prescription", prescription_node)
graph.add_node("human_handoff", human_handoff_node)
graph.add_node("post_call", post_call_node)

graph.set_entry_point("language_router")
```

### Edge definitions

```python
# Agent 1 → Agent 2 (always, unconditional)
graph.add_edge("language_router", "voice_intake")

# Agent 2 → conditional router
graph.add_conditional_edges(
    "voice_intake",
    route_after_intake,
    {
        "scheduler": "scheduler",
        "prescription": "prescription",
        "await_input": END,           # stop here; next user utterance restarts the graph
        "human_handoff": "human_handoff",
    }
)

# Agent 3 → escalation check
graph.add_conditional_edges(
    "scheduler",
    check_escalation,
    {
        "human_handoff": "human_handoff",
        "post_call": "post_call",
    }
)

# Agent 4 → escalation check
graph.add_conditional_edges(
    "prescription",
    check_escalation,
    {
        "human_handoff": "human_handoff",
        "post_call": "post_call",
    }
)

# Human handoff → END
graph.add_edge("human_handoff", END)

# Post-call → END
graph.add_edge("post_call", END)
```

### Routing functions

```python
def route_after_intake(state: AgentState) -> str:
    """Decides which agent handles the patient's intent."""
    if state.get("escalation_required", False):
        return "human_handoff"
    intent = state.get("intent")
    if intent == "book":
        return "scheduler"
    elif intent == "prescription":
        return "prescription"
    else:
        # Intent not clear yet. Voice intake already added a clarifying question
        # to messages. Return END ("await_input") so the CLI/LiveKit can deliver
        # that question and wait for the next user utterance. The next pass
        # re-enters at language_router and voice_intake runs again with the new
        # user message. A self-loop here would re-run voice_intake immediately
        # within the same ainvoke() call, before the patient has replied.
        return "await_input"


def check_escalation(state: AgentState) -> str:
    """After Agent 3 or 4 completes, check if escalation is needed."""
    if state.get("escalation_required", False):
        return "human_handoff"
    return "post_call"
```

---

## Outbound Graph — `graph.py` (second graph)

```python
outbound_graph = StateGraph(AgentState)

outbound_graph.add_node("scheduler_outbound", scheduler_outbound_node)
outbound_graph.add_node("prescription_outbound", prescription_outbound_node)
outbound_graph.add_node("followup_outbound", followup_outbound_node)
outbound_graph.add_node("escalate", escalate_node)

outbound_graph.set_entry_point("route_job")
outbound_graph.add_node("route_job", route_outbound_job_node)

outbound_graph.add_conditional_edges(
    "route_job",
    route_outbound_job,
    {
        "confirmation": "scheduler_outbound",
        "rx_reminder": "prescription_outbound",
        "followup": "followup_outbound",
    }
)

# Agent 5 (followup) → risk check
outbound_graph.add_conditional_edges(
    "followup_outbound",
    check_risk,
    {
        "escalate": "escalate",
        "end": END,
    }
)

# Others → END directly
outbound_graph.add_edge("scheduler_outbound", END)
outbound_graph.add_edge("prescription_outbound", END)
outbound_graph.add_edge("escalate", END)
```

### Outbound routing

```python
def route_outbound_job(state: AgentState) -> str:
    """Reads the job_type field set by the cron trigger."""
    job_type = state.get("job_type")  # Set by APScheduler before invoking graph
    if job_type == "confirmation":
        return "confirmation"
    elif job_type == "rx_reminder":
        return "rx_reminder"
    elif job_type == "followup":
        return "followup"
    raise ValueError(f"Unknown job_type: {job_type}")


def check_risk(state: AgentState) -> str:
    """After Agent 5 follow-up, check readmission risk."""
    outcome = state.get("call_outcome", {})
    risk_score = outcome.get("readmission_risk", 0.0)
    if risk_score > 0.7:
        return "escalate"
    return "end"
```

### Cron trigger (APScheduler)

```python
# In api/main.py — register this on app startup
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("interval", minutes=30)
async def run_outbound_jobs():
    """Query due_followups and pending_confirmations from Supabase.
    For each due job, invoke the outbound LangGraph with the right job_type."""
    due_jobs = await get_due_outbound_jobs()  # queries Supabase
    for job in due_jobs:
        initial_state = {
            "patient_id": job["patient_id"],
            "lang_code": job["lang_code"],
            "tts_voice": job["tts_voice"],
            "job_type": job["job_type"],  # "confirmation" | "rx_reminder" | "followup"
            "messages": [],
            "current_agent": "route_job",
            "escalation_required": False,
        }
        await outbound_graph.ainvoke(initial_state)

scheduler.start()  # Called in FastAPI lifespan
```

---

## The 5 Agents — Implementation Details

### Agent 1 — Language Router (`agent_language_router.py`)

**Role:** Always runs first. Detects language, sets voice persona. No DB calls. No LLM.

**Implementation:**

```python
async def language_router_node(state: AgentState) -> AgentState:
    state["current_agent"] = "language_router"

    # 1. Check Upstash Redis for cached language preference
    cached_lang = await redis_get(f"lang_pref:{state.get('patient_id', 'unknown')}")
    if cached_lang:
        lang_config = load_language_config(cached_lang)
        return {
            **state,
            "lang_code": cached_lang,
            "tts_voice": lang_config["tts_voice"],
            "tts_model": lang_config["tts_model"],
        }

    # 2. If no cache, STT auto-detects from first utterance.
    #    Saaras v3 with language="unknown" returns detected_language in response metadata.
    #    The LiveKit agent session handles this — we just read the detected lang
    #    from the STT response metadata.
    detected_lang = state.get("detected_language") or "hi-IN"  # Default to Hindi

    # 3. Fallback: If STT detection confidence is missing or low (< 0.6), call
    #    Sarvam Language ID API. Missing confidence happens when the message came
    #    from plain text (CLI test) with no STT metadata.
    detection_confidence = state.get("detection_confidence")
    if detection_confidence is None or detection_confidence < 0.6:
        detected_lang = await sarvam_identify_language(state["messages"][-1]["content"])

    # 4. Load language config from languages.yaml
    lang_config = load_language_config(detected_lang)

    return {
        **state,
        "lang_code": detected_lang,
        "tts_voice": lang_config["tts_voice"],
        "tts_model": lang_config["tts_model"],
    }
```

**Key behaviors:**
- Pure routing — no conversation with the patient here
- Reads from Upstash Redis FIRST (skip detection on repeat callers)
- Falls back to Sarvam Language ID API if STT confidence is low
- Handles mid-call language switches: if any downstream agent detects a language change in the utterance, control returns here to re-route

---

### Agent 2 — Voice Intake (`agent_voice_intake.py`)

**Role:** Collects patient identity and intent. Registers new patients silently in background.

**Sarvam APIs used:** sarvam-30b (structured extraction), Saaras v3 STT

**Tool calls:**
- `get_patient_record(phone: str) -> dict | None` — looks up patient in Supabase by phone
- `register_patient(name, phone, age, lang_pref) -> str` — creates patient in Supabase, returns patient_id

**System prompt (core logic):**

```
You are a hospital receptionist voice agent. You are speaking to a patient in {lang_code}.
Your job is to:
1. Greet them warmly in their language
2. Ask for their name and phone number
3. Determine their intent: do they want to book an appointment, ask about their prescription, or something else?
4. Extract: {name, phone, age, department, urgency, intent}

Rules:
- If get_patient_record returns None, call register_patient silently (passing name, phone, age, lang_pref).
  DO NOT tell the patient they are being registered. Continue the conversation naturally.
- If intent is unclear after the first exchange, ask ONE clarifying question. Max 3 rounds.
- After 3 unclear rounds, set escalation_required=True.
- Always respond in {lang_code}. Handle code-mixing naturally (Hinglish, Marathlish).
- Extract urgency from context: "bahut dard" / "emergency" / "jaldi" = "urgent". Default is "normal".
- If you know the intent but the patient hasn't given a phone number yet, ask for it before proceeding.

Output JSON:
{
  "patient_name": "...",
  "phone": "...",
  "age": 0,
  "intent": "book" | "prescription" | "followup" | "query",
  "department": "...",
  "urgency": "normal" | "urgent"
}
```

**Key behaviors:**

- **Structured intake checklist (`intake_collected`):** On every turn, `voice_intake_node` reads `state["intake_collected"]`, passes it to the system prompt via `build_voice_intake_prompt(lang_code, already_collected)`, and merges new non-None extractions back in. Fields set in earlier turns are never overwritten with `null`. This means:
  - Turn 1: patient says "I want an appointment" → `collected = {intent: "book"}`
  - Turn 2: patient says "9876543210" → LLM sees intent already collected, extracts only phone → `collected = {intent: "book", phone: "9876543210"}` → proceeds to DB lookup
  - The LLM is never given a blank slate — it only fills in what's still missing.

- **Phone-first gate:** If `intent` is known but `phone` is still `None` and no `patient_id` exists yet, the node appends a language-specific phone prompt and returns with top-level `intent=None` so `route_after_intake` emits `"await_input"` → `END`. The intent is preserved in `intake_collected` and will be picked up next turn without re-asking.

- **Silent registration:** If `get_patient_record(phone)` returns None, call `register_patient(name, phone, age, lang_pref)` immediately. The patient hears no pause.

- **Max 3 clarification loops:** tracks `intake_attempt_count` in state. After 3 unclear rounds (intent still None), sets `escalation_required=True` and the graph routes to Human Handoff.

- **Writes `recent_calls`** summary to Upstash Redis at the end of the call (via post-call subgraph).

---

### Agent 3 — Appointment Scheduler (`agent_scheduler.py`)

**Role:** Books, reschedules, or cancels OPD appointments. Core business logic agent.

**Sarvam APIs used:** sarvam-30b (reasoning + conversation), Bulbul v3 TTS, Mayura v1 Translate

**Tool calls:**
- `check_available_slots(department: str, date: str) -> List[dict]` — queries Supabase appointments table
- `get_next_available(department: str, n: int = 3) -> List[dict]` — if no slots on requested date, returns next 3 available across any date
- `book_slot(patient_id: str, slot_id: str) -> dict` — writes to Supabase appointments, returns confirmation dict with `appointment_id, doctor_name, date, time`
- `cancel_appointment(appointment_id: str) -> bool` — updates status to "cancelled"
- `confirm_appointment(appointment_id: str) -> None` — marks appointment as confirmed (used by outbound node)
- `translate_text(text: str, source_lang: str, target_lang: str) -> str` — Sarvam Translate API

**System prompt (core logic):**

```
You are the appointment scheduling agent for a hospital. The patient speaks {lang_code}.

Available tools: check_available_slots, get_next_available, book_slot, cancel_appointment,
                 confirm_appointment, translate_text

Workflow:
1. Patient wants to book → call check_available_slots(department, date)
2. If slots available → present top 3 options in patient's language → patient picks one → call book_slot
3. If NO slots available → call get_next_available(department, 3) → present 3 alternatives across dates
4. After booking → translate the confirmation message to {lang_code} using translate_text
5. The confirmation includes: doctor name, date, time

LLM decision object (action field):
- "clarify"         → ask the patient a clarifying question (reply field holds the text)
- "check_slots"     → call check_available_slots; date field may be "any"
- "confirm_booking" → call book_slot with chosen_slot_id
- "cancel"          → call cancel_appointment with cancel_appointment_id
- "reschedule"      → cancel old slot, then offer new ones via check_available_slots / get_next_available

Rules:
- Never confirm a booking without the patient explicitly agreeing to the slot
- If the patient seems confused or distressed, set distress=true in the JSON → escalation
- All responses in {lang_code}
```

**Outbound variant (Agent 3 outbound — `scheduler_outbound_node`):**
- Called by cron for appointment confirmations
- Script translated to patient's language: "This is a reminder call about your upcoming appointment tomorrow. Will you be attending?"
- If patient declines or wants to reschedule → delegates to `scheduler_node` (reuses full inbound logic)
- If patient confirms → calls `confirm_appointment(appointment_id)` and closes with a translated confirmation
- Sets `call_outcome: {"confirmed": True}` on success

---

### Agent 4 — Prescription Reminder (`agent_prescription.py`)

**Role:** Handles existing patients asking about their medication schedule.

**Sarvam APIs used:** sarvam-30b (medical Q&A), Bulbul v3 TTS, Mayura v1 Translate

**Tool calls:**
- `get_prescription(patient_id: str) -> dict` — queries Supabase prescriptions table; raises `ValueError` if no prescription on file
- `translate_text(text: str, source_lang: str, target_lang: str) -> str` — translates doctor notes from English
- `log_query(patient_id: str, query: str, response: str) -> None` — logs to Supabase call_logs
- `mark_reminder_sent(patient_id: str) -> None` — marks outbound reminder as sent in Supabase (outbound node only)

**System prompt (core logic):**

```
You are the prescription assistant for a hospital. The patient speaks {lang_code}.

Available tools: get_prescription, translate_text, log_query

Workflow:
1. Call get_prescription(patient_id) — if it raises ValueError (no prescription on file),
   escalate immediately with a translated apology message.
2. The prescription contains: medicines (JSON array), notes_en (English text), refill_date
3. Translate notes_en to {lang_code} using translate_text
4. Read out the medication schedule in the patient's language
5. Answer any follow-up questions: "can I take with food?", "what are the side effects?", "when to refill?"

LLM decision object fields: reply (text to speak), escalate (bool)

Rules:
- Doctor notes are ALWAYS in English. You MUST translate them before reading to the patient.
- DO NOT give medical advice beyond what's in the prescription. For anything beyond the prescription,
  set escalate=true — the node escalates to human handoff automatically.
- Log every query-response pair via log_query for doctor review.
- All responses in {lang_code}
```

**Outbound variant (Agent 4 outbound — `prescription_outbound_node`):**
- Called by cron for medication reminders
- Fetches prescription via `get_prescription`; if no prescription found, logs `reminder_sent: False` and exits gracefully (no escalation)
- Script translated to patient's language: "This is a reminder call about your medication: [medicine names]. Please take it as prescribed."
- Calls `mark_reminder_sent(patient_id)` after delivering the script
- Sets `call_outcome: {"reminder_sent": True}` on success

---

### Agent 5 — Post-Discharge Follow-up (`agent_followup.py`)

**Role:** Outbound only. Calls discharged patients at 24h/72h to check recovery status.

**Sarvam APIs used:** All four — STT (listen to patient), LLM (reason about symptoms), TTS (speak checklist), Translate (notes to regional lang)

**Tool calls:**
- `get_discharge_info(patient_id: str) -> dict` — queries Supabase discharge_followups
- `log_outcome(patient_id: str, outcome: dict) -> None` — writes structured outcome to Supabase
- `escalate_to_doctor(patient_id: str, reason: str) -> None` — fires Slack webhook + SMS to on-call doctor

**System prompt (core logic):**

```
You are the post-discharge follow-up agent. You are calling a patient who was discharged from the hospital.
The patient speaks {lang_code}. This is an OUTBOUND call — YOU initiate.

Available tools: get_discharge_info, log_outcome, escalate_to_doctor

Workflow:
1. Call get_discharge_info(patient_id) to know: discharge_date, diagnosis, medications_prescribed
2. Greet the patient in their language: "Namaste, main [hospital name] se bol raha hoon..."
3. Ask the structured symptom checklist:
   a. "Aapko bukhaar toh nahi hai?" → fever: yes/no
   b. "Dard ka level 1 se 10 mein kitna hai?" → pain_level: 1-10
   c. "Kya aap apni dawai le rahe hain?" → medication_adherence: yes/no/partial
   d. "Koi aur problem toh nahi hai?" → additional_concerns: free text
4. Compute readmission_risk:
   - fever=yes OR pain_level>7 OR medication_adherence=no → risk = 0.8
   - pain_level 4-7 AND medication_adherence=partial → risk = 0.5
   - All normal → risk = 0.2
5. Call log_outcome with structured JSON: {fever, pain_level, medication_adherence, additional_concerns, readmission_risk}
6. If readmission_risk > 0.7 → call escalate_to_doctor

Rules:
- Be empathetic. The patient is recovering. Speak slowly and clearly.
- If the patient is unresponsive or the call goes to voicemail, log outcome as "unreachable" and retry in 4 hours.
- All conversation in {lang_code}.
```

**Structured output:**

```python
class FollowupOutcome(TypedDict):
    fever: bool
    pain_level: int               # 1-10
    medication_adherence: str     # "yes" | "no" | "partial"
    additional_concerns: str
    readmission_risk: float       # 0.0-1.0
    status: str                   # "completed" | "unreachable" | "escalated"
```

---

## LiveKit Integration — `voice/livekit_agent.py`

This file bridges LiveKit's audio stream to the LangGraph graph.

**Critical config — use EXACTLY these settings:**

```python
from livekit.agents import AgentSession
from livekit.plugins import sarvam

stt = sarvam.STT(
    language="unknown",         # REQUIRED — auto-detect, never hardcode
    model="saaras:v3",
    mode="transcribe",
    flush_signal=True           # REQUIRED — proper turn detection
)

llm = sarvam.LLM(model="sarvam-30b")  # NOT 105b — latency matters

tts = sarvam.TTS(
    target_language_code=state["lang_code"],  # Dynamic from Agent 1
    model="bulbul:v3",
    speaker=state["tts_voice"]                # Dynamic from languages.yaml
)

session = AgentSession(
    turn_detection="stt",          # REQUIRED — Sarvam handles VAD internally
    min_endpointing_delay=0.07     # REQUIRED — matches Saaras v3 processing
)
# DO NOT pass vad= to AgentSession — Sarvam handles this internally
```

**How the session loop works:**

```python
async def on_message(message: str, state: AgentState):
    """Called by LiveKit when STT produces a transcription."""
    # 1. Append user message to state
    state["messages"].append({"role": "user", "content": message})

    # 2. Run one step of the LangGraph graph
    result = await inbound_graph.ainvoke(state)

    # 3. Get the assistant's response from the updated messages
    assistant_message = result["messages"][-1]["content"]

    # 4. TTS speaks the response (LiveKit handles audio routing)
    return assistant_message
```

**Important:** The LangGraph graph runs ONE FULL PASS per user utterance. It doesn't stream token-by-token to the patient during graph execution — it completes the graph traversal, produces a final response, and TTS speaks it. This is by design: the patient hears a complete, coherent sentence, not fragments.

---

## Tool Functions — `agents/tools/`

### `language_config.py` — Language config loader

```python
def load_language_config(lang_code: str) -> dict:
    """Load tts_voice, tts_model, greeting from config/languages.yaml for the given lang_code.
    Used by Agent 1 to set voice persona after language detection."""
```

### `llm_json.py` — LLM response parser

```python
def extract_json(text: str) -> dict | None:
    """Parse the first JSON object found in an LLM reply string.
    Returns None if the reply is plain text (a conversational clarification).
    All agents use this to handle cases where the model responds conversationally
    instead of outputting pure JSON."""
```

### `db_tools.py` — Supabase operations

```python
# All functions use the Supabase Python client (supabase-py)
# Connection is initialized once in api/database.py and imported here

async def get_patient_record(phone: str) -> dict | None:
    """Look up patient by phone number. Returns patient dict or None."""

async def register_patient(name: str, phone: str, age: int, lang_pref: str) -> str:
    """Create new patient in Supabase. Returns patient_id (UUID)."""

async def check_available_slots(department: str, date: str) -> list[dict]:
    """Query appointments table for open slots. Returns list of {slot_id, doctor_name, time, department}."""

async def get_next_available(department: str, n: int = 3) -> list[dict]:
    """If no slots on requested date, get next N available across all dates."""

async def book_slot(patient_id: str, slot_id: str) -> dict:
    """Book an appointment. Updates slot status to 'booked'. Returns confirmation dict."""

async def cancel_appointment(appointment_id: str) -> bool:
    """Cancel an appointment. Updates status to 'cancelled'."""

async def confirm_appointment(appointment_id: str) -> None:
    """Mark appointment as confirmed (used by outbound scheduler confirmation calls)."""

async def get_prescription(patient_id: str) -> dict:
    """Get most recent prescription for a patient. Returns {medicines, notes_en, refill_date}.
    Raises ValueError if no prescription exists."""

async def mark_reminder_sent(patient_id: str) -> None:
    """Mark medication reminder as sent in Supabase (used by outbound prescription node)."""

async def get_discharge_info(patient_id: str) -> dict:
    """Get discharge info for follow-up. Returns {discharge_date, diagnosis, medications}.
    Raises ValueError if no discharge record exists."""

async def log_outcome(patient_id: str, outcome: dict) -> None:
    """Write follow-up outcome to discharge_followups table."""

async def log_query(patient_id: str, query: str, response: str) -> None:
    """Log a prescription query-response pair to call_logs for doctor review."""

async def get_due_outbound_jobs() -> list[dict]:
    """Query discharge_followups and appointments for due outbound jobs. Called by cron."""
```

### `redis_tools.py` — Upstash Redis operations

```python
# Uses upstash-redis Python client (HTTP-based, works everywhere)

from upstash_redis import Redis

redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)

async def redis_get(key: str) -> str | None:
    """Get a value from Upstash Redis."""
    return await redis.get(key)

async def redis_set(key: str, value: str, ttl_seconds: int) -> None:
    """Set a value with TTL in Upstash Redis."""
    await redis.set(key, value, ex=ttl_seconds)

async def save_call_summary(patient_id: str, summary: str) -> None:
    """Append call summary to recent_calls list (max 5, TTL 7 days)."""
    key = f"recent_calls:{patient_id}"
    await redis.lpush(key, summary)
    await redis.ltrim(key, 0, 4)  # Keep last 5
    await redis.expire(key, 7 * 24 * 3600)  # 7 days

async def save_session_state(call_id: str, state_json: str) -> None:
    """Save call state snapshot for crash recovery (TTL 30 min)."""
    await redis.set(f"session:{call_id}", state_json, ex=1800)

async def save_lang_preference(patient_id: str, lang_code: str) -> None:
    """Cache patient's language preference (TTL 90 days)."""
    await redis.set(f"lang_pref:{patient_id}", lang_code, ex=90 * 24 * 3600)

async def get_recent_calls(patient_id: str) -> list[str]:
    """Get last 5 call summaries for context."""
    return await redis.lrange(f"recent_calls:{patient_id}", 0, 4)
```

### `translate_tools.py` — Sarvam Translate wrapper

```python
from sarvamai import SarvamAI

client = SarvamAI(api_subscription_key=SARVAM_API_KEY)

async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using Sarvam Mayura v1.
    Primary use: doctor notes (en-IN) → patient language (hi-IN, mr-IN)."""
    response = client.text.translate(
        input=text,
        source_language_code=source_lang,
        target_language_code=target_lang,
    )
    return response.translated_text
```

### `notification_tools.py` — Slack + SMS

```python
import httpx
from twilio.rest import Client

async def send_slack_alert(message: str) -> None:
    """Send escalation alert to Slack channel via webhook."""
    async with httpx.AsyncClient() as client:
        await client.post(SLACK_WEBHOOK_URL, json={"text": message})

async def send_sms(phone: str, message: str) -> None:
    """Send SMS via Twilio."""
    twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    twilio_client.messages.create(
        body=message,
        from_=TWILIO_PHONE_NUMBER,
        to=phone,
    )

async def escalate_to_doctor(patient_id: str, reason: str) -> None:
    """Fire both Slack alert and SMS to on-call doctor."""
    patient = await get_patient_record_by_id(patient_id)
    message = f"🚨 ESCALATION: Patient {patient['name']} (ID: {patient_id})\nReason: {reason}\nLang: {patient['lang_pref']}\nPhone: {patient['phone']}"
    await send_slack_alert(message)
    # SMS to on-call doctor (hardcoded for demo, would be from a roster table in production)
    await send_sms(ON_CALL_DOCTOR_PHONE, message)
```

---

## Post-Call Subgraph — `analytics/call_analytics.py`

Runs after every inbound call completes. This is a LangGraph node, not a separate graph.

```python
async def post_call_node(state: AgentState) -> AgentState:
    """Post-call analytics: Batch STT + diarization + sarvam-30b analysis."""
    state["current_agent"] = "post_call"

    # 1. Save call summary to Redis (Layer 2)
    summary = generate_call_summary(state["messages"])
    await save_call_summary(state["patient_id"], summary)

    # 2. Save language preference to Redis (Layer 2)
    await save_lang_preference(state["patient_id"], state["lang_code"])

    # 3. Run Sarvam Batch STT + Diarization on the recording
    if state.get("call_recording_path"):
        transcript = await sarvam_batch_stt(
            audio_path=state["call_recording_path"],
            model="saaras:v3",
            with_diarization=True,
        )

        # 4. Run sarvam-30b analysis on the diarized transcript
        analysis = await sarvam_analyze_call(
            transcript=transcript,
            analysis_points=[
                "sentiment_score",        # -1.0 to 1.0
                "issue_resolved",         # bool
                "agent_talk_time_pct",    # 0-100
                "patient_talk_time_pct",  # 0-100
                "call_duration_sec",
                "key_topics",             # list of strings
            ]
        )

        # 5. Write to Supabase call_logs
        await save_call_log(
            patient_id=state["patient_id"],
            call_id=state["call_id"],
            recording_path=state["call_recording_path"],
            analytics_json=analysis,
            duration=analysis.get("call_duration_sec"),
            outcome=state.get("call_outcome"),
        )

    # 6. If this was a booking, schedule outbound confirmation at +2h
    if state.get("intent") == "book":
        await schedule_outbound_job(
            patient_id=state["patient_id"],
            job_type="confirmation",
            due_at=now() + timedelta(hours=2),
        )

    # 7. Check if patient has a recent discharge — schedule follow-up
    discharge = await get_pending_discharge(state["patient_id"])
    if discharge and discharge["status"] == "pending":
        await schedule_outbound_job(
            patient_id=state["patient_id"],
            job_type="followup",
            due_at=discharge["due_at"],
        )

    return {**state, "call_outcome": analysis}
```

---

## Testing Checklist

Before deploying, verify each agent works in isolation:

1. **Agent 1:** Feed it a Hindi utterance → should return `lang_code="hi-IN"`, `tts_voice="priya"`
2. **Agent 1:** Feed it Marathi → should return `lang_code="mr-IN"`, `tts_voice="kavya"`
3. **Agent 1:** Repeat caller with cached `lang_pref` in Redis → should skip detection entirely
4. **Agent 2:** New patient (phone not in DB) → should call `register_patient` silently, no audible pause
5. **Agent 2:** Patient states intent only (no phone) → `intake_collected.intent` locked in; next turn with phone number only → proceeds to DB lookup without re-asking intent
6. **Agent 2:** Ambiguous intent → should ask clarifying question, max 3 rounds; `intake_collected` accumulates partial fields across rounds
6. **Agent 3:** Request slot on a full day → should offer 3 alternatives via `get_next_available`
7. **Agent 3:** Book → cancel → rebook → verify DB state is consistent
8. **Agent 4:** Get prescription → verify `notes_en` is translated to patient's `lang_code`
9. **Agent 5:** Follow-up with `pain_level=8` → should set `readmission_risk=0.8` → escalate
10. **Post-call:** Verify Redis gets the call summary, Supabase gets the analytics JSON

---

## Observability — LangSmith

**Why LangSmith:** LangGraph is built by LangChain. Every `graph.ainvoke()` call is auto-traced with zero instrumentation code — each node (language_router → voice_intake → scheduler → post_call) becomes a child span automatically. STT and TTS require thin subclass wrappers in `voice/livekit_agent.py`.

**Free tier:** 5,000 traces/month. 1 trace = 1 `graph.ainvoke()` call (≈ 1 patient utterance). Sufficient for demo load (~27 full conversations/day). At real clinic scale (50+ calls/day) the $39/month tier (50K traces) is needed.

### Environment variables

```bash
LANGCHAIN_TRACING_V2=true          # set false to disable without code change
LANGCHAIN_API_KEY=sk-lc-...
LANGCHAIN_PROJECT=hospital-receptionist
```

Setting these env vars is sufficient to enable LangGraph auto-tracing. No code changes needed in agent files.

### What is traced automatically (zero code)

Every `inbound_graph.ainvoke(state, config={"metadata": {"session_id": ..., "call_id": ...}})` produces one **trace** in LangSmith:

```
Trace  (searchable by session_id)
  ├── Run: language_router      latency, input/output state
  ├── Run: voice_intake         latency, input/output state
  │     └── LLM: sarvam-30b    prompt tokens, completion tokens, latency
  ├── Run: scheduler            latency, input/output state
  │     └── LLM: sarvam-30b    prompt tokens, completion tokens, latency
  └── Run: post_call            latency, input/output state
```

`session_id` is passed as LangSmith metadata on every `ainvoke()` so all turns of one patient conversation are groupable in the UI.

### STT and TTS spans

STT and TTS happen outside the LangGraph graph in `voice/livekit_agent.py`. They are wrapped via thin subclasses `_TracedSTT` and `_TracedTTS` that call `langsmith.trace()` around each `recognize()` / `synthesize()` call:

```python
with ls_trace("stt", metadata={"session_id": ..., "model": "saaras:v3", "operation": "stt"}):
    return await super().recognize(...)

with ls_trace("tts", metadata={"session_id": ..., "model": "bulbul:v3", "char_count": len(text)}):
    return await super().synthesize(text, ...)
```

### Metrics visible in LangSmith UI

| Metric | How captured |
|---|---|
| LLM call count per session | Count of LLM child runs under each trace |
| LLM latency per agent | Automatically on every LLM child run |
| LLM token usage | Automatically (input + output tokens per call) |
| Per-node latency | Automatically per LangGraph node |
| STT latency | `_TracedSTT.recognize()` span |
| TTS latency | `_TracedTTS.synthesize()` span |
| End-to-end turn latency | Trace start → last node end |

### Files changed

| File | Change |
|---|---|
| `.env.example` | Added 3 LangSmith env vars |
| `requirements.txt` | Added `langsmith>=0.1.0` |
| `voice/livekit_agent.py` | `_TracedSTT` + `_TracedTTS` subclasses; `session_id` in `ainvoke` metadata |
| `test_cli.py` | `session_id` passed as metadata on every `ainvoke` call |

No changes to any agent files — LangGraph auto-instruments them via env vars.

---

*This file is the single source of truth for the agent system. Do not contradict it in code.*