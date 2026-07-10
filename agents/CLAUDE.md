# agents/CLAUDE.md — LangGraph Multi-Agent System

> **Read root CLAUDE.md first.** This file covers the LangGraph implementation:
> both graphs (inbound + outbound), all 5 agents + 2 new agents (Lab Status, Billing),
> shared state, tools, the LiveKit integration layer, observability, and agentic design patterns.

---

## Architecture Overview

Two separate LangGraph `StateGraph` instances:

1. **Inbound Graph** — triggered when a patient calls in or opens the website.
   Flow: START → Agent 1 → Agent 2 → intent? → Agent 3/4/6/7 → escalate? → Post-Call Subgraph → END
2. **Outbound Graph** — triggered by APScheduler cron every 30 minutes.
   Flow: Cron → job_type? → Agent 3/4/5 outbound → risk? → Escalate → END

Both graphs share the same `AgentState` TypedDict and the same tool functions.

### Agentic Design Patterns in This System

Nine distinct patterns are present. Understanding them helps you implement correctly
and explains to interviewers why each design decision was made.

| # | Pattern | Where | Category |
|---|---|---|---|
| 1 | **Router / Dispatcher** | `route_after_intake()` fans intent → specialist agent | Orchestration |
| 2 | **Sequential Pipeline** | Agent 1 → Agent 2 always runs first, unconditional | Orchestration |
| 3 | **Human-in-the-Loop** | Any agent sets `escalation_required=True` → `human_handoff_node` | Reliability |
| 4 | **Stateful Multi-Turn** | `intake_collected` accumulates fields across turns, never re-asks | Memory |
| 5 | **Multi-Layer Memory** | RAM (AgentState) → Redis (TTL) → Supabase (permanent) | Memory |
| 6 | **Tool-Calling / ReAct** | Agents 3, 4, 5 — LLM picks tool, calls it, observes result | Action |
| 7 | **Guardrails** | Input (emergency check, confidence) + Output (lang consistency, medical boundary) | Reliability |
| 8 | **Event-Driven / Cron Subgraph** | APScheduler → outbound graph — proactive, not reactive | Action |
| 9 | **Post-Processing Subgraph** | `post_call_node` — analytics + scheduling after patient hangs up | Action |

**Pattern notes for implementation:**
- Patterns 1 and 2 together explain the graph structure: pipeline first (fixed), router second (conditional).
- Pattern 4 (`intake_collected`) is the most important UX pattern — without it patients repeat themselves.
- Pattern 6 (Tool-Calling) applies to Agents 3/4/5 only. Agents 1, 6, 7 do NOT use LLM tool-calling — they call one predetermined function.
- Pattern 7 guardrails run at the LiveKit session layer, NOT inside the graph. They fire even if the graph crashes.
- Pattern 8 is what makes this system proactive. Most voice agents are purely reactive.

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
    intent: Optional[Literal[
        "book", "prescription", "followup", "query",
        "lab", "billing"                    # NEW — Agent 6 and Agent 7
    ]]
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
    current_agent: str                      # "language_router" | "voice_intake" | "scheduler"
                                            # | "prescription" | "lab_status" | "billing"
                                            # | "followup" | "human_handoff" | "post_call"
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
- `intent` now has 6 valid values: `"book"`, `"prescription"`, `"followup"`, `"query"`, `"lab"`, `"billing"`. Agent 2 extracts all six. Only the new two route to new agents.

---

## Inbound Graph — `graph.py`

### Node definitions

```python
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)

# Existing nodes — DO NOT change
graph.add_node("language_router", language_router_node)
graph.add_node("voice_intake", voice_intake_node)
graph.add_node("scheduler", scheduler_node)
graph.add_node("prescription", prescription_node)
graph.add_node("human_handoff", human_handoff_node)
graph.add_node("post_call", post_call_node)

# NEW nodes — Agent 6 and Agent 7
graph.add_node("lab_status", lab_status_node)      # agents/agent_lab_status.py
graph.add_node("billing", billing_node)            # agents/agent_billing.py

graph.set_entry_point("language_router")
```

### Edge definitions

```python
# Agent 1 → Agent 2 (always, unconditional) — UNCHANGED
graph.add_edge("language_router", "voice_intake")

# Agent 2 → conditional router — UPDATED with 2 new branches
graph.add_conditional_edges(
    "voice_intake",
    route_after_intake,
    {
        "scheduler":    "scheduler",
        "prescription": "prescription",
        "lab_status":   "lab_status",      # NEW
        "billing":      "billing",         # NEW
        "await_input":  END,               # stop; next utterance re-enters at language_router
        "human_handoff": "human_handoff",
    }
)

# Agent 3 → escalation check — UNCHANGED
graph.add_conditional_edges(
    "scheduler",
    check_escalation,
    {"human_handoff": "human_handoff", "post_call": "post_call"}
)

# Agent 4 → escalation check — UNCHANGED
graph.add_conditional_edges(
    "prescription",
    check_escalation,
    {"human_handoff": "human_handoff", "post_call": "post_call"}
)

# Agent 6 → escalation check — NEW (same pattern as A3/A4)
graph.add_conditional_edges(
    "lab_status",
    check_escalation,
    {"human_handoff": "human_handoff", "post_call": "post_call"}
)

# Agent 7 → escalation check — NEW (same pattern as A3/A4)
graph.add_conditional_edges(
    "billing",
    check_escalation,
    {"human_handoff": "human_handoff", "post_call": "post_call"}
)

# Terminals — UNCHANGED
graph.add_edge("human_handoff", END)
graph.add_edge("post_call", END)
```

### Routing functions

```python
def route_after_intake(state: AgentState) -> str:
    """Decides which agent handles the patient's intent.
    UPDATED: added 'lab' and 'billing' branches. All other logic unchanged."""
    if state.get("escalation_required", False):
        return "human_handoff"
    intent = state.get("intent")
    if intent == "book":
        return "scheduler"
    elif intent == "prescription":
        return "prescription"
    elif intent == "lab":           # NEW
        return "lab_status"
    elif intent == "billing":       # NEW
        return "billing"
    else:
        # Intent not clear yet. Voice intake already added a clarifying question
        # to messages. Return END ("await_input") so the CLI/LiveKit can deliver
        # that question and wait for the next user utterance. The next pass
        # re-enters at language_router and voice_intake runs again with the new
        # user message. A self-loop here would re-run voice_intake immediately
        # within the same ainvoke() call, before the patient has replied.
        return "await_input"


def check_escalation(state: AgentState) -> str:
    """After any specialist agent completes, check if escalation is needed.
    Used by Agents 3, 4, 6, and 7. UNCHANGED."""
    if state.get("escalation_required", False):
        return "human_handoff"
    return "post_call"
```

---

## Outbound Graph — `graph.py` (second graph)

**UNCHANGED from previous version.** Reproduced here for reference.

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
        "rx_reminder":  "prescription_outbound",
        "followup":     "followup_outbound",
    }
)

outbound_graph.add_conditional_edges(
    "followup_outbound",
    check_risk,
    {"escalate": "escalate", "end": END}
)

outbound_graph.add_edge("scheduler_outbound", END)
outbound_graph.add_edge("prescription_outbound", END)
outbound_graph.add_edge("escalate", END)
```

### Outbound routing + cron trigger — UNCHANGED

```python
def route_outbound_job(state: AgentState) -> str:
    job_type = state.get("job_type")
    if job_type == "confirmation":  return "confirmation"
    elif job_type == "rx_reminder": return "rx_reminder"
    elif job_type == "followup":    return "followup"
    raise ValueError(f"Unknown job_type: {job_type}")

def check_risk(state: AgentState) -> str:
    outcome = state.get("call_outcome", {})
    risk_score = outcome.get("readmission_risk", 0.0)
    if risk_score > 0.7: return "escalate"
    return "end"
```

```python
# In api/main.py — register this on app startup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
scheduler = AsyncIOScheduler()

@scheduler.scheduled_job("interval", minutes=30)
async def run_outbound_jobs():
    due_jobs = await get_due_outbound_jobs()
    for job in due_jobs:
        initial_state = {
            "patient_id": job["patient_id"],
            "lang_code":  job["lang_code"],
            "tts_voice":  job["tts_voice"],
            "job_type":   job["job_type"],
            "messages":   [],
            "current_agent": "route_job",
            "escalation_required": False,
        }
        await outbound_graph.ainvoke(initial_state)

scheduler.start()
```

---

## The 7 Agents — Implementation Details

### Agent 1 — Language Router (`agent_language_router.py`)

**UNCHANGED.** Reproduced for completeness.

**Role:** Always runs first. Detects language, sets voice persona. No DB calls. No LLM.

```python
async def language_router_node(state: AgentState) -> AgentState:
    state["current_agent"] = "language_router"

    # 1. Check Upstash Redis for cached language preference
    cached_lang = await redis_get(f"lang_pref:{state.get('patient_id', 'unknown')}")
    if cached_lang:
        lang_config = load_language_config(cached_lang)
        return {**state, "lang_code": cached_lang, "tts_voice": lang_config["tts_voice"], "tts_model": lang_config["tts_model"]}

    # 2. STT auto-detects. Read detected_language from STT metadata.
    detected_lang = state.get("detected_language") or "hi-IN"

    # 3. Fallback: if confidence missing or < 0.6, call Sarvam Language ID API
    detection_confidence = state.get("detection_confidence")
    if detection_confidence is None or detection_confidence < 0.6:
        detected_lang = await sarvam_identify_language(state["messages"][-1]["content"])

    lang_config = load_language_config(detected_lang)
    return {**state, "lang_code": detected_lang, "tts_voice": lang_config["tts_voice"], "tts_model": lang_config["tts_model"]}
```

---

### Agent 2 — Voice Intake (`agent_voice_intake.py`)

**UPDATED: two new intent values added to system prompt and `intake_collected`.** All existing logic unchanged.

**Role:** Collects patient identity and intent. Registers new patients silently.

**Tool calls:** `get_patient_record(phone)`, `register_patient(name, phone, age, lang_pref)`

**System prompt update — add these two lines to the intent classification:**

```
# EXISTING intents (unchanged):
# book, prescription, followup, query

# NEW intents to detect:
# "lab"     → patient asks about lab/blood test report status
#             Signals: "report aayi kya", "blood test result", "mera report ready hai kya"
# "billing" → patient asks about bill, payment, outstanding amount
#             Signals: "bill kitna hai", "payment karna hai", "kitna baki hai", "outstanding amount"

Output JSON — intent field now accepts 6 values:
{
  "patient_name": "...",
  "phone": "...",
  "age": 0,
  "intent": "book" | "prescription" | "followup" | "query" | "lab" | "billing",
  "department": "...",
  "urgency": "normal" | "urgent"
}
```

**Key behaviors (all unchanged from previous version):**
- `intake_collected` accumulates partial fields across turns — never re-asks
- Phone-first gate: intent known but phone missing → prompt for phone, preserve intent in `intake_collected`
- Silent registration: `get_patient_record` returns None → call `register_patient` immediately
- Max 3 clarification loops → `escalation_required=True`

---

### Agent 3 — Appointment Scheduler (`agent_scheduler.py`) — UNCHANGED

**Tool calls:** `check_available_slots`, `get_next_available`, `book_slot`, `cancel_appointment`, `confirm_appointment`, `translate_text`

No changes. See previous version for full system prompt and tool call details.

---

### Agent 4 — Prescription Reminder (`agent_prescription.py`) — UNCHANGED

**Tool calls:** `get_prescription`, `translate_text`, `log_query`, `mark_reminder_sent`

No changes. See previous version for full system prompt and tool call details.

---

### Agent 5 — Post-Discharge Follow-up (`agent_followup.py`) — UNCHANGED

**Tool calls:** `get_discharge_info`, `log_outcome`, `escalate_to_doctor`

No changes. See previous version for full system prompt and tool call details.

---

### Agent 6 — Lab Status (`agent_lab_status.py`) — NEW

**Role:** Looks up lab/diagnostic report status for a patient. Pure lookup — no LLM reasoning.

**Pattern used:** NOT Tool-Calling/ReAct. This agent calls ONE predetermined tool, translates the result, speaks it. No LLM decision about which tool to call.

**Sarvam APIs used:** Translate (Mayura v1) + TTS (Bulbul v3) only. No LLM call.

**Tool calls:**
- `get_lab_status(patient_id: str) -> list[dict]` — returns all ready/pending reports
- `mark_report_dispatched(report_id: str) -> None` — flips status to 'dispatched' after reading

**Implementation:**

```python
async def lab_status_node(state: AgentState) -> AgentState:
    state["current_agent"] = "lab_status"

    reports = await get_lab_status(state["patient_id"])

    if not reports:
        # No reports on file — speak apology + escalate is NOT needed, just inform
        message = await translate_text(
            "No lab reports are currently on file for you. Please contact the lab counter.",
            source_lang="en-IN", target_lang=state["lang_code"]
        )
        state["messages"].append({"role": "assistant", "content": message})
        return state

    # Filter: only read READY reports that haven't been dispatched yet
    ready = [r for r in reports if r["status"] == "ready"]
    pending = [r for r in reports if r["status"] == "pending"]

    if ready:
        # Read each ready report's result_summary_en translated to patient's language
        for report in ready:
            translated_summary = await translate_text(
                report["result_summary_en"],
                source_lang="en-IN", target_lang=state["lang_code"]
            )
            message = f"{report['test_name']}: {translated_summary}"
            state["messages"].append({"role": "assistant", "content": message})
            await mark_report_dispatched(report["report_id"])

    if pending:
        # Tell patient which tests are still pending
        test_names = ", ".join([r["test_name"] for r in pending])
        pending_msg = await translate_text(
            f"The following tests are still being processed: {test_names}. Please check back later.",
            source_lang="en-IN", target_lang=state["lang_code"]
        )
        state["messages"].append({"role": "assistant", "content": pending_msg})

    # No escalation needed unless something unexpected happens
    return state
```

**New DB table: `lab_reports`**

```python
class LabReport(Base):
    __tablename__ = "lab_reports"

    report_id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id        = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    test_name         = Column(String(200), nullable=False)       # "Blood CBC", "Lipid Panel", "HbA1c"
    status            = Column(String(20), default="pending")     # "pending" | "ready" | "dispatched"
    ordered_at        = Column(DateTime, default=datetime.utcnow)
    ready_at          = Column(DateTime, nullable=True)
    result_summary_en = Column(String(500), nullable=True)        # English summary — translate before TTS
```

**New tool functions to add to `db_tools.py`:**

```python
async def get_lab_status(patient_id: str) -> list[dict]:
    """Get all pending and ready lab reports for a patient.
    Returns [{report_id, test_name, status, ready_at, result_summary_en}]
    Excludes 'dispatched' reports — those have already been read to the patient."""

async def mark_report_dispatched(report_id: str) -> None:
    """Mark a report as dispatched after reading it to the patient.
    Prevents repeated read-out of the same report on subsequent calls."""
```

**Seed data to add in `seed.py`:**

```python
lab_reports = [
    # Ready report — will be read out in demo (Ramesh Kumar, hi-IN)
    {
        "patient_id": patients[0]["id"],
        "test_name": "Complete Blood Count (CBC)",
        "status": "ready",
        "ready_at": "2026-07-06T14:00:00",
        "result_summary_en": "Hemoglobin is slightly low at 10.8 g/dL. All other values are within normal range. Please follow up with your doctor.",
    },
    # Pending report — demo shows "still processing" message (Arun Patil, mr-IN)
    {
        "patient_id": patients[2]["id"],
        "test_name": "Lipid Panel",
        "status": "pending",
        "ready_at": None,
        "result_summary_en": None,
    },
    # Already dispatched — should NOT appear in demo (to verify filtering)
    {
        "patient_id": patients[0]["id"],
        "test_name": "Blood Glucose",
        "status": "dispatched",
        "ready_at": "2026-07-05T09:00:00",
        "result_summary_en": "Blood glucose is 98 mg/dL, within normal fasting range.",
    },
]
```

---

### Agent 7 — Billing (`agent_billing.py`) — NEW

**Role:** Reads outstanding bill amount and dispatches UPI payment link via SMS. Pure lookup — no LLM reasoning.

**Pattern used:** NOT Tool-Calling/ReAct. Calls two predetermined tools in sequence. No LLM decision.

**Sarvam APIs used:** Translate (Mayura v1) + TTS (Bulbul v3) only. No LLM call.

**Tool calls:**
- `get_bill(patient_id: str) -> dict | None` — most recent unpaid bill
- `dispatch_payment_link(bill_id: str, phone: str) -> None` — sends UPI link via Twilio SMS

**Implementation:**

```python
async def billing_node(state: AgentState) -> AgentState:
    state["current_agent"] = "billing"

    bill = await get_bill(state["patient_id"])

    if not bill:
        message = await translate_text(
            "No outstanding bills found for your account.",
            source_lang="en-IN", target_lang=state["lang_code"]
        )
        state["messages"].append({"role": "assistant", "content": message})
        return state

    # Format amount in Indian numbering (₹3,200)
    amount_str = f"₹{bill['amount_due']:,.0f}"

    # Translate bill summary to patient's language
    bill_summary_en = f"Your outstanding bill is {amount_str}."
    if bill.get("items_json"):
        items = ", ".join([f"{i['desc']} ({i['amount']})" for i in bill["items_json"][:3]])
        bill_summary_en += f" This includes: {items}."

    translated = await translate_text(bill_summary_en, source_lang="en-IN", target_lang=state["lang_code"])
    state["messages"].append({"role": "assistant", "content": translated})

    # Always dispatch payment link via SMS — patient always gets it after hearing the amount
    # No need to ask — this is the primary resolution action for billing intent
    if bill.get("payment_link") and state.get("patient_id"):
        patient = await get_patient_record_by_id(state["patient_id"])
        await dispatch_payment_link(bill["bill_id"], patient["phone"])

        link_msg = await translate_text(
            "A payment link has been sent to your registered mobile number.",
            source_lang="en-IN", target_lang=state["lang_code"]
        )
        state["messages"].append({"role": "assistant", "content": link_msg})

    return state
```

**Design decision: payment link is always dispatched automatically.**
No confirmation prompt needed — when a patient calls about their bill, the payment link
is the primary resolution. Asking "kya aap link chahte hain?" adds a turn without value.

**New DB table: `bills`**

```python
class Bill(Base):
    __tablename__ = "bills"

    bill_id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id   = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False, index=True)
    amount_due   = Column(Numeric(10, 2), nullable=False)
    status       = Column(String(20), default="unpaid")      # "unpaid" | "partial" | "paid"
    items_json   = Column(JSON, default=list)                # [{desc, qty, amount}]
    payment_link = Column(Text, nullable=True)               # UPI deep link
    created_at   = Column(DateTime, default=datetime.utcnow)
```

**New tool functions to add to `db_tools.py`:**

```python
async def get_bill(patient_id: str) -> dict | None:
    """Get most recent unpaid/partial bill for a patient.
    Returns {bill_id, amount_due, status, items_json, payment_link} or None."""

async def dispatch_payment_link(bill_id: str, phone: str) -> None:
    """Send UPI payment link to patient's phone via Twilio SMS.
    Reuses send_sms() from notification_tools.py — no new infra."""
```

**`dispatch_payment_link` implementation:**

```python
async def dispatch_payment_link(bill_id: str, phone: str) -> None:
    bill = await get_bill_by_id(bill_id)
    message = f"Pay your hospital bill of ₹{bill['amount_due']:.0f} here: {bill['payment_link']}\n— Hospital Receptionist"
    await send_sms(phone, message)  # reuses existing send_sms() from notification_tools.py
```

**Seed data to add in `seed.py`:**

```python
bills = [
    # Unpaid bill — demo reads amount + dispatches link (Sunita Devi, hi-IN)
    {
        "patient_id": patients[1]["id"],
        "amount_due": 3200.00,
        "status": "unpaid",
        "items_json": [
            {"desc": "OPD Consultation", "qty": 1, "amount": 500},
            {"desc": "Blood CBC Test", "qty": 1, "amount": 700},
            {"desc": "Medicines", "qty": 1, "amount": 2000},
        ],
        "payment_link": "upi://pay?pa=hospital@okaxis&am=3200&cu=INR&tn=HospitalBill",
    },
    # Paid bill — should NOT appear in demo (verify get_bill filters correctly)
    {
        "patient_id": patients[3]["id"],
        "amount_due": 1500.00,
        "status": "paid",
        "items_json": [{"desc": "OPD Consultation", "qty": 1, "amount": 1500}],
        "payment_link": None,
    },
]
```

---

## LiveKit Integration — `voice/livekit_agent.py` — UNCHANGED

Critical config — use EXACTLY these settings:

```python
from livekit.agents import AgentSession
from livekit.plugins import sarvam

stt = sarvam.STT(
    language="unknown",    # REQUIRED — auto-detect, never hardcode
    model="saaras:v3",
    mode="transcribe",
    flush_signal=True      # REQUIRED — proper turn detection
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

```python
async def on_message(message: str, state: AgentState):
    state["messages"].append({"role": "user", "content": message})
    result = await inbound_graph.ainvoke(state)
    assistant_message = result["messages"][-1]["content"]
    return assistant_message
```

---

## Tool Functions — `agents/tools/`

### `language_config.py` — UNCHANGED

```python
def load_language_config(lang_code: str) -> dict:
    """Load tts_voice, tts_model, greeting from config/languages.yaml."""
```

### `llm_json.py` — UNCHANGED

```python
def extract_json(text: str) -> dict | None:
    """Parse the first JSON object found in an LLM reply string.
    Returns None if the reply is plain text (a conversational clarification)."""
```

### `db_tools.py` — UPDATED (new functions appended, nothing removed)

All existing functions from previous version remain unchanged. New additions:

```python
# ── Existing functions (ALL UNCHANGED) ──
async def get_patient_record(phone: str) -> dict | None: ...
async def register_patient(name: str, phone: str, age: int, lang_pref: str) -> str: ...
async def check_available_slots(department: str, date: str) -> list[dict]: ...
async def get_next_available(department: str, n: int = 3) -> list[dict]: ...
async def book_slot(patient_id: str, slot_id: str) -> dict: ...
async def cancel_appointment(appointment_id: str) -> bool: ...
async def confirm_appointment(appointment_id: str) -> None: ...
async def get_prescription(patient_id: str) -> dict: ...        # raises ValueError if not found
async def mark_reminder_sent(patient_id: str) -> None: ...
async def get_discharge_info(patient_id: str) -> dict: ...      # raises ValueError if not found
async def log_outcome(patient_id: str, outcome: dict) -> None: ...
async def log_query(patient_id: str, query: str, response: str) -> None: ...
async def get_due_outbound_jobs() -> list[dict]: ...

# ── NEW functions for Agent 6 (Lab Status) ──
async def get_lab_status(patient_id: str) -> list[dict]:
    """Get all 'pending' and 'ready' lab reports for a patient.
    Excludes 'dispatched' reports — already delivered.
    Returns [{report_id, test_name, status, ready_at, result_summary_en}]"""

async def mark_report_dispatched(report_id: str) -> None:
    """Flip lab_reports.status from 'ready' to 'dispatched'.
    Prevents re-reading the same report on the next call."""

# ── NEW functions for Agent 7 (Billing) ──
async def get_bill(patient_id: str) -> dict | None:
    """Get most recent unpaid or partial bill for a patient.
    Returns {bill_id, amount_due, status, items_json, payment_link} or None.
    Filters: status IN ('unpaid', 'partial'), orders by created_at DESC, LIMIT 1."""

async def get_bill_by_id(bill_id: str) -> dict:
    """Get a specific bill by bill_id. Used by dispatch_payment_link."""

async def dispatch_payment_link(bill_id: str, phone: str) -> None:
    """Fetch bill, format SMS message with UPI link, call send_sms().
    Reuses existing send_sms() from notification_tools.py — no new infra."""
```

### `redis_tools.py` — UNCHANGED

All three key patterns preserved. No changes.

```python
# Key patterns (unchanged):
# recent_calls:{patient_id}  — TTL 7 days
# session:{session_id}       — TTL 30 min
# lang_pref:{patient_id}     — TTL 90 days
```

### `translate_tools.py` — UNCHANGED

```python
async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Sarvam Mayura v1. Primary use: EN → hi-IN / mr-IN."""
```

### `notification_tools.py` — UNCHANGED

`send_sms()` is reused by `dispatch_payment_link()` — no changes needed to this file.

---

## Post-Call Subgraph — `analytics/call_analytics.py` — UNCHANGED

No changes needed. The post-call node already handles `intent == "book"` for scheduling outbound jobs.
Lab and billing calls do not trigger outbound jobs, so no new branches are needed.

```python
# post_call_node fires after ALL specialist agents including A6 and A7
# For lab/billing calls: saves call summary to Redis, lang_pref to Redis,
# runs batch STT analytics, writes to call_logs — same as A3/A4 calls.
# No new conditional branches needed.
```

---

## Guardrails — `voice/livekit_agent.py`

**Guardrails run at the LiveKit session layer, NOT inside the LangGraph graph.**
This means they fire even if the graph crashes mid-execution.

### Input guardrails (fire BEFORE graph.ainvoke)

```python
# 1. Emergency detection — hard block, bypasses all agents
EMERGENCY_KEYWORDS = {
    "hi-IN": ["दिल का दौरा", "सांस नहीं", "बेहोश", "ब्रेन स्ट्रोक"],
    "mr-IN": ["हृदयविकाराचा झटका", "श्वास नाही", "बेशुद्ध"],
    "en-IN": ["heart attack", "not breathing", "unconscious", "stroke"],
}
# If any keyword matches → speak emergency number immediately → set escalation_required=True

# 2. STT confidence check — soft block
# If stt.confidence < 0.3 AND duration < 1s → ask patient to speak again (max 3 retries)

# 3. Unsupported language — soft block
# If Language ID returns lang not in languages.yaml after 3 utterances → human_handoff
```

### Output guardrails (fire AFTER graph.ainvoke, BEFORE TTS)

```python
# 4. Language consistency check
# Run Sarvam Language ID on LLM output
# If detected lang ≠ state.lang_code → discard, retry with explicit instruction (max 2 retries)

# 5. Medical boundary check (Agent 4 outputs only)
MEDICAL_ADVICE_PATTERNS = ["you should take", "increase dosage", "stop medication", "avoid this medicine"]
# If pattern detected → discard → replace with "please consult your doctor" → escalate

# 6. TTS length cap
# If response > 300 chars → summarise before TTS
# Log original response to Supabase for review

# 7. PII scrub before logging
# Redact phone numbers, aadhaar patterns, DOB from call_logs
# Store patient_id FK instead of raw PII in analytics tables
```

---

## Observability — LangSmith

**Why LangSmith:** LangGraph auto-instruments every `graph.ainvoke()` call with zero code changes.
Each node (language_router → voice_intake → lab_status → post_call) becomes a child span automatically.

**Free tier:** 5,000 traces/month. Sufficient for demo (≈1 trace per patient utterance).

### Setup — environment variables only, no code changes to agents

```bash
LANGCHAIN_TRACING_V2=true          # set false to disable without code change
LANGCHAIN_API_KEY=sk-lc-...
LANGCHAIN_PROJECT=hospital-receptionist
```

### What is traced automatically

Every `inbound_graph.ainvoke(state, config={"metadata": {"session_id": ..., "call_id": ...}})`:

```
Trace  (searchable by session_id)
  ├── Run: language_router        latency, input/output state
  ├── Run: voice_intake           latency, input/output state
  │     └── LLM: sarvam-30b      prompt tokens, completion tokens, latency
  ├── Run: lab_status             latency, input/output state   ← NEW, traced automatically
  │     (no LLM child — pure tool call)
  ├── Run: billing                latency, input/output state   ← NEW, traced automatically
  │     (no LLM child — pure tool call)
  └── Run: post_call              latency, input/output state
```

### STT and TTS spans — thin wrappers in `livekit_agent.py`

```python
with ls_trace("stt", metadata={"session_id": ..., "model": "saaras:v3", "operation": "stt"}):
    return await super().recognize(...)

with ls_trace("tts", metadata={"session_id": ..., "model": "bulbul:v3", "char_count": len(text)}):
    return await super().synthesize(text, ...)
```

### Files changed for observability

| File | Change |
|---|---|
| `.env.example` | 3 LangSmith env vars |
| `requirements.txt` | `langsmith>=0.1.0` |
| `voice/livekit_agent.py` | `_TracedSTT` + `_TracedTTS` subclasses; `session_id` in `ainvoke` metadata |
| `agents/agent_lab_status.py` | No changes needed — auto-traced |
| `agents/agent_billing.py` | No changes needed — auto-traced |

---

## Testing Checklist

### Existing agents (unchanged behavior — verify nothing broke)

1. **Agent 1:** Hindi utterance → `lang_code="hi-IN"`, `tts_voice="priya"`
2. **Agent 1:** Marathi → `lang_code="mr-IN"`, `tts_voice="kavya"`
3. **Agent 1:** Repeat caller with cached `lang_pref` → skips detection entirely
4. **Agent 2:** New patient → `register_patient` called silently, no audible pause
5. **Agent 2:** Patient states intent only (no phone) → `intake_collected.intent` locked in; next turn with phone → proceeds without re-asking
6. **Agent 2:** Ambiguous intent → clarifying question, max 3 rounds
7. **Agent 3:** Full day requested → 3 alternatives via `get_next_available`
8. **Agent 3:** Book → cancel → rebook → DB state consistent
9. **Agent 4:** Prescription → `notes_en` translated to `lang_code`
10. **Agent 5:** `pain_level=8` → `readmission_risk=0.8` → escalate
11. **Post-call:** Redis gets call summary, Supabase gets analytics JSON

### New agents (Agent 6 and Agent 7)

12. **Agent 6 — ready report:** Patient with `report_id` in "ready" status → result_summary_en translated → spoken → `mark_report_dispatched` called → status flips to "dispatched"
13. **Agent 6 — pending only:** Patient with only "pending" reports → "still processing" message in their language
14. **Agent 6 — no reports:** Patient with no lab_reports rows → "no reports on file" message, no escalation
15. **Agent 6 — repeat call:** Patient calls again after report dispatched → "dispatched" row filtered out → treated as "no reports" (verify `get_lab_status` excludes "dispatched")
16. **Agent 7 — unpaid bill:** Patient with unpaid bill → amount spoken in language → UPI link SMS dispatched to patient phone
17. **Agent 7 — no bill:** Patient with no unpaid bills → "no outstanding bills" message
18. **Agent 7 — paid bill:** Patient whose only bill has `status="paid"` → same as "no bill" (verify `get_bill` filters correctly)
19. **Router — lab intent:** Utterance "meri report aayi kya" → Agent 2 extracts `intent="lab"` → `route_after_intake` returns `"lab_status"` → Agent 6 runs
20. **Router — billing intent:** Utterance "mera bill kitna hai" → Agent 2 extracts `intent="billing"` → `route_after_intake` returns `"billing"` → Agent 7 runs
21. **Escalation path — A6/A7:** Manually set `escalation_required=True` in lab_status_node → verify `check_escalation` routes to `human_handoff` → Slack webhook fires

---

*This file is the single source of truth for the agent system. Do not contradict it in code.*