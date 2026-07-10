# frontend/CLAUDE.md — Next.js Frontend + Talking Voice Assistant

> **Read root CLAUDE.md first.** This file covers the Next.js frontend:
> the full talking voice assistant UI, call lifecycle management,
> LiveKit integration, stored responses, admin dashboard, and Vercel deployment.

---

## Core Concept — What This Frontend Is

This is a **hands-free talking voice assistant**, not a push-to-talk app.

The flow from the patient's perspective:
1. Open the page → see language selector + "Start Call" button
2. Select language (Hindi / Marathi / Auto) → click "Start Call"
3. Agent immediately speaks the greeting in their language
4. Patient talks → agent listens → agent responds via voice
5. This loops until patient clicks "End Call" or call drops
6. Call end is always handled cleanly — whether user-initiated or abrupt drop

No mic button to hold. No typing. Pure voice conversation.

---

## Stack

- **Framework:** Next.js 14+ with App Router
- **Language:** TypeScript
- **Styling:** Tailwind CSS
- **Voice:** LiveKit React SDK (`@livekit/components-react`)
- **Charts (admin):** Recharts
- **Deploy:** Vercel (free tier, GitHub auto-deploy)

---

## Pages

### `/` — Talking Voice Assistant (main page)

```
┌────────────────────────────────────────────────────────────────┐
│  ● Swastha AI                                                  │
│    Hospital Voice Receptionist                                 │
│                                                                │
│  ┌──────────────────────┐  ┌───────────────────────────────┐  │
│  │  CALL CONTROL CARD   │  │  Session | Duration | Language │  │
│  │  (min-height 340px)  │  ├───────────────────────────────┤  │
│  │                      │  │  Live Transcript               │  │
│  │  [Hindi][Marathi]    │  │                                │  │
│  │  [Auto]              │  │  Agent: नमस्ते! मैं आपकी      │  │
│  │                      │  │  क्या मदद कर सकता हूँ?        │  │
│  │  ▶ Start Call        │  │                                │  │
│  │                      │  │  Patient: मुझे appointment    │  │
│  │  ── IN-CALL ──        │  │  चाहिए                        │  │
│  │  ◉ Listening...      │  │                                │  │
│  │  ≋ waves             │  └───────────────────────────────┘  │
│  │  ■ End Call          │                                      │
│  │  simulate dropped    │                                      │
│  │                      │                                      │
│  │  ── ENDED ──          │                                      │
│  │  Call Summary        │                                      │
│  │  Duration / Language │                                      │
│  │  Intent / Agents     │                                      │
│  │  [Start New Call]    │                                      │
│  └──────────────────────┘                                      │
│                                                                │
│  ┌──────────────────────┐  (booking card appears when booked) │
│  │  Appointment Card    │                                      │
│  └──────────────────────┘                                      │
└────────────────────────────────────────────────────────────────┘
```

**Layout:** Fixed two-column grid: `300px 1fr`. No responsive breakpoint — the screen itself
is max-width 960px and scrolls on small viewports.

Left column (300px fixed):
- Call control card: all four states (idle / in-call / ended / dropped) live in ONE card
  with `min-height: 340px`, `flex-col items-center justify-center gap-16px text-center`
- Booking / Lab / Bill cards appear below the call control card when agents fire those events

Right column (1fr):
- **Session bar**: Session ID (HSP-XXXX monospace) | Duration (MM:SS counter) | Language (accent color)
- **Live Transcript**: scrollable, `height: 456px`, inset shadow

The AgentActivityFeed component is NOT rendered in the main layout — the session bar +
transcript replace it. State tracking for agents still happens in VoiceAssistant for
future use but is not visualized on the main page.

### `/admin` — Admin Dashboard

```
┌────────────────────────────────────────────────────────┐
│  Admin Dashboard                                       │
│                                                        │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────────┐          │
│  │  47  │  │ 2:34 │  │ 89%  │  │ 3 pending │          │
│  │calls │  │ avg  │  │resol.│  │follow-ups │          │
│  └──────┘  └──────┘  └──────┘  └──────────┘          │
│                                                        │
│  ┌──────────────────┐  ┌──────────────────────────┐   │
│  │ Language         │  │ Agent Activations         │   │
│  │ Breakdown (pie)  │  │ (bar chart)               │   │
│  └──────────────────┘  └──────────────────────────┘   │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │ Latency Panel (last 50 calls, from Langfuse)   │    │
│  │ STT p50: 250ms | LLM TTFT p50: 1.94s | Total: 2.5s│
│  └────────────────────────────────────────────────┘    │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │ Recent Calls table                             │    │
│  │ Time | Lang | Intent | Agents | Duration       │    │
│  └────────────────────────────────────────────────┘    │
│                                                        │
│  ┌────────────────────────────────────────────────┐    │
│  │ Pending Follow-ups table                       │    │
│  │ Patient | Discharge Date | Due At | Status     │    │
│  └────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────┘
```

---

## Call Lifecycle — Complete State Machine

This is the most important section. Every component's behavior derives from this.

```
IDLE
  │  Patient selects language → clicks "Start Call"
  ▼
CONNECTING  (POST /api/token → join LiveKit room)
  │  Room joined + agent worker joins same room
  ▼
GREETING    (agent speaks opening line via session.say())
  │  "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?"
  │  Stored response — no LLM call for the greeting
  ▼
LISTENING   (mic open, VAD active, waiting for patient to speak)
  │  Patient speaks
  ▼
PROCESSING  (STT → LangGraph agents → response generated)
  │  current_agent changes as agents activate
  ▼
SPEAKING    (agent TTS audio plays back to patient)
  │  allow_interruptions=False — patient must wait
  │  Agent finishes speaking
  ▼
LISTENING   (loops back — continuous conversation)
  │
  │  ─── patient clicks "End Call" ───────────────────┐
  │  ─── LiveKit disconnects (network drop, timeout) ─┤
  │  ─── escalation_required=True (human handoff) ────┤
  ▼                                                   ▼
ENDING      (teardown in progress)              CALL_DROPPED
  │                                                   │
  ▼                                                   ▼
ENDED       (show call summary, reset UI)       DROPPED_SUMMARY
            (POST /api/followup/log if needed)  (same as ENDED
                                                 + show reconnect prompt)
```

### State transitions in React

```typescript
type CallStatus =
  | "idle"           // pre-call, show Start Call button
  | "connecting"     // token fetch + LiveKit room join
  | "greeting"       // agent playing opening message
  | "listening"      // mic open, waiting for patient
  | "processing"     // STT→LangGraph→TTS in progress
  | "speaking"       // agent audio playing back
  | "ending"         // user clicked End Call, cleanup running
  | "ended"          // call finished cleanly
  | "call_dropped"   // LiveKit disconnected unexpectedly

const [callStatus, setCallStatus] = useState<CallStatus>("idle");
```

---

## Components

### `CallControls.tsx` — Start Call + End Call buttons

This is the primary UI control. Replaces the old `VoiceButton.tsx`.

```typescript
// PRE-CALL state (callStatus === "idle"):
//   Shows: language selector + "Start Call" button (green, phone icon)
//   "Start Call" click triggers:
//     1. setCallStatus("connecting")
//     2. POST /api/token → get LiveKit JWT
//     3. Connect LiveKitRoom (connect={true})
//     4. Send preferred_lang as room metadata

// IN-CALL state (callStatus !== "idle" && !== "ended" && !== "call_dropped"):
//   Shows: animated status indicator + "End Call" button (red, phone-off icon)
//   Status indicator:
//     - connecting: pulsing gray ring "Connecting..."
//     - greeting:   pulsing blue ring "Agent speaking..."
//     - listening:  pulsing green ring with sound wave animation "Listening..."
//     - processing: spinning purple ring "Processing..."
//     - speaking:   pulsing purple ring with speaker animation "Agent speaking..."
//     - ending:     gray ring "Ending call..."
//   "End Call" click triggers:
//     1. setCallStatus("ending")
//     2. room.disconnect()  — graceful LiveKit disconnect
//     3. POST /api/followup/log if booking was made during call
//     4. setCallStatus("ended")
//     5. show CallSummaryCard

// DROPPED state (callStatus === "call_dropped"):
//   Shows: red warning "Call dropped unexpectedly"
//   Shows: "Reconnect" button + "End Session" button
//   Reconnect: goes back to CONNECTING with same room name
//   End Session: goes to ENDED

// Key implementation detail:
// The "End Call" button must ALWAYS be visible during a call —
// even while agent is speaking. This is the abrupt drop handler.
// Never hide or disable End Call once a call has started.
```

**Call drop detection:**

```typescript
// In the LiveKitRoom onDisconnected callback:
onDisconnected={(reason) => {
  if (callStatus === "ending") {
    // User-initiated — go to ended cleanly
    setCallStatus("ended");
  } else {
    // Unexpected drop — network issue, Railway restart, timeout
    setCallStatus("call_dropped");
    // Log the dropped call to Supabase
    logDroppedCall(callId, reason);
  }
}}
```

---

### `VoiceAssistant.tsx` — Orchestrator component

This is the top-level component that wires everything together.

```typescript
// This component owns:
// - callStatus state
// - token fetch logic
// - LiveKitRoom connection
// - all event handlers
// - layout

export default function VoiceAssistant() {
  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [token, setToken] = useState<string | null>(null);
  const [preferredLang, setPreferredLang] = useState<string>("auto");
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [bookingDetails, setBookingDetails] = useState<BookingDetails | null>(null);
  const [labResults, setLabResults] = useState<LabResult[] | null>(null);   // Agent 6
  const [billDetails, setBillDetails] = useState<BillDetails | null>(null); // Agent 7
  const callId = useRef(`hospital-${Date.now()}`);

  const startCall = async () => {
    setCallStatus("connecting");
    const res = await fetch("/api/token", {
      method: "POST",
      body: JSON.stringify({
        room: callId.current,
        participant: `patient-${Math.random().toString(36).slice(2,8)}`,
        preferred_lang: preferredLang,
      }),
    });
    const { token } = await res.json();
    setToken(token);
    // LiveKitRoom connect={!!token} will now connect
  };

  const endCall = async () => {
    setCallStatus("ending");
    // room.disconnect() called by CallControls
    // cleanup handled in onDisconnected callback
  };

  return (
    <LiveKitRoom
      token={token}
      serverUrl={process.env.NEXT_PUBLIC_LIVEKIT_URL}
      connect={!!token}
      onConnected={() => setCallStatus("greeting")}
      onDisconnected={(reason) => handleDisconnect(reason)}
      // Pass preferred_lang as room metadata so Agent 1 can read it
      options={{ roomMetadata: JSON.stringify({ preferred_lang: preferredLang }) }}
    >
      <RoomAudioRenderer />  {/* REQUIRED — plays agent TTS audio */}
      <AgentEventHandler
        onTranscript={(msg) => setTranscript(prev => [...prev, msg])}
        onAgentChange={(agent) => setActiveAgent(agent)}
        onStatusChange={(status) => setCallStatus(status)}
        onBookingConfirmed={(details) => setBookingDetails(details)}
        onLabResult={(reports) => setLabResults(reports)}   // NEW Agent 6
        onBillRead={(details) => setBillDetails(details)}   // NEW Agent 7
      />
      <div className="grid grid-cols-2 gap-6 p-6">
        <div>
          <CallControls
            callStatus={callStatus}
            preferredLang={preferredLang}
            onLangChange={setPreferredLang}
            onStartCall={startCall}
            onEndCall={endCall}
          />
          {bookingDetails && <BookingConfirmationCard details={bookingDetails} />}
          {labResults    && <LabResultCard reports={labResults} />}
          {billDetails   && <BillCard details={billDetails} />}
        </div>
        <div>
          <AgentActivityFeed activeAgent={activeAgent} callStatus={callStatus} />
          <TranscriptPanel messages={transcript} />
        </div>
      </div>
    </LiveKitRoom>
  );
}
```

**Do NOT forget `<RoomAudioRenderer />`** inside `<LiveKitRoom>`. Without it the patient cannot hear the agent speak. This is the single most common LiveKit React mistake.

---

### `AgentEventHandler.tsx` — Data channel listener

Invisible component. Listens to LiveKit data channel events from the agent worker and surfaces them to the UI.

```typescript
// Agent worker sends these events via LiveKit data channel ("agent-events"):
type AgentEvent =
  | { type: "transcript";        role: "user" | "assistant"; content: string; timestamp: string }
  | { type: "agent_change";      agent: string }
    // agent values: "language_router" | "voice_intake" | "scheduler"
    //             | "prescription" | "lab_status" | "billing" | "followup"
  | { type: "status_change";     status: CallStatus }
  | { type: "booking_confirmed"; details: BookingDetails }
  | { type: "lab_result_ready";  reports: { test_name: string; summary: string }[] }
    // Fired by Agent 6 when ready reports are found — show a lab result card in UI
  | { type: "bill_read";         amount: number; sms_sent: boolean }
    // Fired by Agent 7 after reading bill — show a billing card in UI
  | { type: "call_dropped";      reason: string }
  | { type: "error";             message: string }

function AgentEventHandler({ onTranscript, onAgentChange, onStatusChange, onBookingConfirmed }) {
  useDataChannel("agent-events", (msg) => {
    const event: AgentEvent = JSON.parse(new TextDecoder().decode(msg.payload));
    switch (event.type) {
      case "transcript":       onTranscript(event); break;
      case "agent_change":     onAgentChange(event.agent); break;
      case "status_change":    onStatusChange(event.status); break;
      case "booking_confirmed": onBookingConfirmed(event.details); break;
      case "lab_result_ready": onLabResult?.(event.reports); break;    // NEW Agent 6
      case "bill_read":        onBillRead?.(event); break;             // NEW Agent 7
      case "error":            console.error("Agent error:", event.message); break;
    }
  });
  return null; // renders nothing
}
```

---

### `LanguageSelector.tsx` — Language picker (pre-call only)

```typescript
// Only shown when callStatus === "idle"
// Hidden (or greyed out) once a call has started

// Options: Hindi | Marathi | Auto-detect
// Default: Auto-detect
// On selection: updates preferredLang in VoiceAssistant state
// On call start: sent as room metadata → Agent 1 reads this:
//   if preferred_lang !== "auto" → skip language detection, use directly

// Visual: 3 pill buttons, selected = filled
// Hindi = blue-600, Marathi = purple-600, Auto = gray-500
```

---

### `TranscriptPanel.tsx` — Live conversation transcript

```typescript
// Messages: [{role: "user"|"assistant", content: string, timestamp: string}]
// Color: patient messages = blue-50 bg + blue-700 text
//        agent messages = purple-50 bg + purple-700 text
// Auto-scroll to latest message
// Show timestamp on hover
// "Patient" label in Hindi/Marathi depending on lang_code:
//   hi-IN: "मरीज़" / "एजेंट"
//   mr-IN: "रुग्ण" / "एजेंट"
//   default: "Patient" / "Agent"

// First message is always the greeting:
// { role: "assistant", content: "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?", timestamp: "..." }
// This comes via the transcript event from the agent worker, not hardcoded in frontend
```

---

### `AgentActivityFeed.tsx` — Agent pipeline visualizer

```typescript
// Shows all 5 agents as a vertical list
// State per agent: "pending" | "active" | "completed"

const AGENTS = [
  { id: "language_router", label: "Language Router",      sublabel: "Detects language" },
  { id: "voice_intake",    label: "Voice Intake",         sublabel: "Understands intent" },
  { id: "scheduler",       label: "Appointment Scheduler", sublabel: "Books slots" },
  { id: "prescription",    label: "Prescription Agent",   sublabel: "Reads medication" },
  { id: "lab_status",      label: "Lab Status",           sublabel: "Report lookup" },
  { id: "billing",         label: "Billing Agent",        sublabel: "Bill + payment link" },
  { id: "followup",        label: "Follow-up Agent",      sublabel: "Post-discharge" },
];

// Note: on any given call only 2-3 agents activate.
// language_router and voice_intake always activate.
// scheduler, prescription, lab_status, or billing activates based on intent.
// followup only activates on outbound cron calls.
// Show all 7 — inactive ones display as pending (○ gray dot).

// Visual per agent:
// pending:   ○ gray circle  + gray label (not yet activated)
// active:    ◉ pulsing blue circle + bold label + sublabel showing current action
// completed: ● solid green circle + dimmed label

// This feed is the key recruiter differentiator.
// Make the active state visually prominent — large pulsing dot, bold text, visible sublabel.
// It shows the multi-agent LangGraph workflow happening live.
```

---

### `BookingConfirmationCard.tsx` — Success state after booking

```typescript
// Appears below CallControls when booking_confirmed event received
// Stays visible for the rest of the call + after call ends

// Content:
// ✅ Appointment Confirmed
// Doctor: Dr. Priya Sharma
// Department: General Medicine
// Date: Tuesday, 8 July 2026
// Time: 10:00 AM
// [Add to Calendar] button (generates .ics file)

// Language-aware labels:
//   hi-IN: "अपॉइंटमेंट कन्फर्म हो गया"
//   mr-IN: "अपॉइंटमेंट कन्फर्म झाले"
```

---

### `LabResultCard.tsx` — Shown on lab_result_ready event (Agent 6) — NEW

```typescript
// Appears below CallControls when lab_result_ready event received
// Stays visible for the rest of the call + after call ends
// Neomorphic style: neo-card with neo-inset result rows

// Content when reports found:
// 🧪 Lab Reports
// [test_name]  [summary in patient's language — already translated by Agent 6]
// [test_name]  [summary]
// (one row per ready report)

// Content when only pending:
// 🧪 Lab Reports
// [test_name] — Processing... (amber dot indicator)

// Language-aware header:
//   hi-IN: "आपकी Lab Reports"
//   mr-IN: "तुमचे Lab Reports"
```

### `BillCard.tsx` — Shown on bill_read event (Agent 7) — NEW

```typescript
// Appears below CallControls when bill_read event received
// Stays visible for the rest of the call + after call ends
// Neomorphic style: neo-card with neo-inset amount cell

// Content:
// 💳 Outstanding Bill
// Amount: ₹3,200
// [SMS sent indicator — green check if sms_sent=true]
// "Payment link sent to your mobile number"

// Language-aware labels:
//   hi-IN: "बकाया Bill"  /  "Payment link भेज दिया गया"
//   mr-IN: "बाकी Bill"   /  "Payment link पाठवला गेला"

// If sms_sent=false (no payment_link in DB):
//   Show amount only, no SMS indicator
```

### `CallSummaryCard.tsx` — Shown after call ends

```typescript
// Shown when callStatus === "ended" or "call_dropped"
// Replaces the in-call status indicator

// Content for clean end:
// Call duration: 2m 34s
// Language: Hindi
// Intent: Appointment booked
// Agents used: Language Router → Voice Intake → Scheduler
// [Start New Call] button

// Content for dropped call:
// ⚠ Call dropped unexpectedly
// [Reconnect] button → restores same callId, goes back to CONNECTING
// [Start New Call] button → new callId, fresh start
// Note: partial booking state is preserved in Supabase —
//       if patient reconnects with same phone, Agent 2 picks up context
//       from Redis recent_calls cache
```

---

## Stored Responses — `lib/storedResponses.ts`

Pre-written text responses for deterministic, fast agent outputs.
These are sent to `session.say()` on the **agent side** (Python),
but the frontend transcript panel receives them via data channel like any other message.

Document the structure here so the frontend knows what to expect:

```typescript
// These come FROM the agent worker — frontend just displays them
// Defined in agents/prompts/ on the Python side

STORED_RESPONSE_KEYS = {
  // Greetings — spoken immediately on GREETING status
  "greeting_hi": "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?",
  "greeting_mr": "नमस्कार! मी तुमची काय मदत करू शकतो?",
  "greeting_auto": "Hello! Namaste! Namaskar! How can I help you today?",

  // Slot confirmation — spoken by Agent 3
  "slot_confirmed_hi": "आपका appointment {doctor} के साथ {date} को {time} बजे confirm हो गया है।",
  "slot_confirmed_mr": "तुमची appointment {doctor} यांच्याकडे {date} ला {time} वाजता confirm झाली आहे।",

  // No slots — spoken by Agent 3 before offering alternatives
  "no_slots_hi": "माफ़ कीजिए, उस तारीख को कोई slot उपलब्ध नहीं है। मैं आपको अगले तीन available slots बता सकता हूँ।",
  "no_slots_mr": "माफ करा, त्या दिवशी कोणतेही slot उपलब्ध नाहीत। मी तुम्हाला पुढील तीन available slots सांगतो।",

  // Escalation — spoken before handing off to human
  "escalation_hi": "मैं आपको अभी हमारे receptionist से connect करता हूँ। एक moment रुकिए।",
  "escalation_mr": "मी तुम्हाला आमच्या receptionist शी connect करतो. एक क्षण थांबा।",

  // Lab report — spoken by Agent 6 when report is ready
  "lab_ready_hi": "{test_name} की रिपोर्ट आ गई है। {result_summary}",
  "lab_ready_mr": "{test_name} चा रिपोर्ट आला आहे। {result_summary}",
  "lab_pending_hi": "आपकी {test_name} की जांच अभी प्रक्रिया में है। कृपया बाद में जांचें।",
  "lab_pending_mr": "तुमची {test_name} तपासणी अजून प्रक्रियेत आहे। कृपया नंतर तपासा।",
  "lab_none_hi": "आपके लिए कोई lab report उपलब्ध नहीं है।",
  "lab_none_mr": "तुमच्यासाठी कोणताही lab report उपलब्ध नाही।",

  // Billing — spoken by Agent 7
  "bill_amount_hi": "आपका बकाया bill ₹{amount} है। मैंने आपके registered mobile पर payment link भेज दिया है।",
  "bill_amount_mr": "तुमचे बाकी bill ₹{amount} आहे। मी तुमच्या registered mobile वर payment link पाठवला आहे।",
  "bill_none_hi": "आपके account पर कोई बकाया bill नहीं है।",
  "bill_none_mr": "तुमच्या account वर कोणतेही बाकी bill नाही।",

  // Call end acknowledgement
  "goodbye_hi": "धन्यवाद! आपका दिन शुभ हो।",
  "goodbye_mr": "धन्यवाद! तुमचा दिवस चांगला जाओ।",
}
```

---

## LiveKit React SDK — Key Patterns

### Installing

```bash
npm install @livekit/components-react livekit-client
```

### Room connection lifecycle (updated for talking assistant)

```
Patient selects language → clicks "Start Call"
  → POST /api/token (with preferred_lang in body)
  → get JWT
  → LiveKitRoom connect={true} fires
  → onConnected → setCallStatus("greeting")
  → agent worker on Railway detects new participant, joins room
  → agent calls session.say(greeting) → TTS audio streams to browser
  → RoomAudioRenderer plays greeting audio
  → AgentEventHandler receives { type: "status_change", status: "listening" }
  → setCallStatus("listening") → UI shows "Listening..." indicator
  → patient speaks → STT → LangGraph → TTS → RoomAudioRenderer plays
  → loop continues
  → patient clicks "End Call" OR LiveKit disconnects
  → onDisconnected fires → setCallStatus("ended" | "call_dropped")
  → CallSummaryCard shown
```

### Token proxy — `app/api/token/route.ts`

```typescript
export async function POST(req: Request) {
  const { room, participant, preferred_lang } = await req.json();
  const res = await fetch(`${process.env.BACKEND_URL}/api/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      room_name: room,
      participant_name: participant,
      preferred_lang: preferred_lang ?? "auto",
    }),
  });
  return Response.json(await res.json());
}

// BACKEND_URL is a server-side env var — never exposed to browser
// NEXT_PUBLIC_BACKEND_URL is used for client-side fetch — not needed for token proxy
```

---

## Directory Structure

```
frontend/
├── CLAUDE.md
├── package.json
├── next.config.js
├── tailwind.config.ts
├── tsconfig.json
├── .env.local
│
├── app/
│   ├── layout.tsx
│   ├── page.tsx                     # Renders <VoiceAssistant />
│   ├── admin/
│   │   └── page.tsx                 # Admin dashboard
│   └── api/
│       └── token/
│           └── route.ts             # Token proxy to FastAPI
│
├── components/
│   ├── VoiceAssistant.tsx           # Top-level orchestrator
│   ├── CallControls.tsx             # Start Call + End Call + status indicator
│   ├── LanguageSelector.tsx         # Pre-call language picker
│   ├── AgentEventHandler.tsx        # Data channel listener (invisible)
│   ├── TranscriptPanel.tsx          # Live conversation transcript
│   ├── AgentActivityFeed.tsx        # 5-agent pipeline visualizer
│   ├── BookingConfirmationCard.tsx  # Shown on booking_confirmed event
│   ├── LabResultCard.tsx            # Shown on lab_result_ready event (Agent 6)
│   ├── BillCard.tsx                 # Shown on bill_read event (Agent 7)
│   ├── CallSummaryCard.tsx          # Shown after call ends or drops
│   └── AdminDashboard.tsx           # /admin page charts + tables
│
└── lib/
    ├── livekit.ts                   # LiveKit config + hooks
    ├── storedResponses.ts           # Reference copy of agent stored responses
    └── types.ts                     # Shared TypeScript types
```

---

## Environment Variables

```bash
# frontend/.env.local
NEXT_PUBLIC_LIVEKIT_URL=wss://your-project.livekit.cloud
NEXT_PUBLIC_BACKEND_URL=https://your-railway-app.up.railway.app

# Server-side only (not NEXT_PUBLIC — never exposed to browser)
BACKEND_URL=https://your-railway-app.up.railway.app
```

---

## Styling Notes

**Emerald Ivory palette** — the only palette used in this project:
- Background: `#ece8e2` (warm off-white ivory)
- Shadow dark: `#c2b9aa` / Shadow light: `#fffefb`
- Text: `#4a4640` / Text muted: `#948e82`
- Accent (emerald): `#2f7d6b` — active states, agent bubbles, language highlight
- Green (sage): `#6bb08a` — Start Call button, success
- Red (terracotta): `#c96a55` — End Call button, error
- Amber: `#c99a5b` — processing state

Key UI elements:
- Header: 44px circle with 14px emerald dot + "Swastha AI" (20px 800-weight) + subtitle
- "Start Call" button: 88×88px sage-green circle, ▶ triangle (not phone icon)
- "End Call" button: 60×60px terracotta circle, ■ square stop icon
- Session bar: three sections separated by 1px inset dividers (neo-session-divider)
- Transcript: 456px fixed height, inset shadow box, Devanagari font for Hindi/Marathi
- Language pills: "Hindi" / "Marathi" / "Auto" (three equal-width buttons, active = neo-in-md)
- Font: 'Plus Jakarta Sans' primary, 'Noto Sans Devanagari' for transcript/booking text

### Devanagari font (critical)

Hindi and Marathi text in the transcript must render correctly on all devices.
Add to `app/layout.tsx`:

```typescript
import { Noto_Sans_Devanagari } from "next/font/google";
const devanagari = Noto_Sans_Devanagari({ subsets: ["devanagari"], weight: ["400", "500"] });
```

Apply to the root `<html>` tag. Without this, Devanagari script falls back to
system fonts which vary across OS and may render poorly on Windows.

---

## Call Drop Handling — Implementation Details

This is a required feature, not optional.

### Detection
```typescript
// Reason codes from LiveKit onDisconnected:
// "SERVER_SHUTDOWN"     → Railway restart/deploy — offer reconnect
// "PARTICIPANT_REMOVED" → admin kicked — show message
// "LEAVE"               → normal disconnect — go to ended
// "SIGNAL_CLOSE"        → network issue — offer reconnect
// "ROOM_DELETED"        → room expired — go to ended

const handleDisconnect = (reason?: DisconnectReason) => {
  const userInitiated = callStatus === "ending";
  if (userInitiated) {
    setCallStatus("ended");
  } else {
    setCallStatus("call_dropped");
    logDroppedCall(callId.current, reason);
  }
};
```

### Reconnect flow
```typescript
const reconnect = async () => {
  // Reuse same callId — agent worker will rejoin the same room
  // Redis session:{callId} still has the state if < 30 min ago
  // Agent 2 will resume from recent_calls context
  setCallStatus("connecting");
  await startCall(); // same callId ref, same room name
};
```

### Backend logging on drop
```typescript
const logDroppedCall = async (callId: string, reason?: string) => {
  await fetch(`${process.env.NEXT_PUBLIC_BACKEND_URL}/api/followup/log`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      call_id: callId,
      outcome: { status: "dropped", reason: reason ?? "unknown" },
    }),
  });
};
```

---

## What the Recruiter Sees (updated)

1. Opens URL → sees clean UI with language selector and "Start Call" button
2. Selects Hindi → clicks "Start Call"
3. Hears immediately: "नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?"
4. Speaks naturally — no button to hold, no typing
5. Watches Agent Activity Feed light up: Language Router → Voice Intake → Scheduler
6. Sees transcript filling in real time in Hindi
7. Gets a booking confirmation card with doctor name + time
8. Clicks "End Call" → sees call summary (duration, agents used, intent)
9. Visits /admin → sees call just logged with language, latency, agent activations

The Agent Activity Feed + live transcript + voice greeting is the full demo loop.
A recruiter can experience it in under 3 minutes.

---

## Future Scope (do not implement now)

- **Interruptions** (`allow_interruptions=True`): Patient can speak while agent is talking.
  Agent stops mid-sentence, processes new input. Requires careful UX — must show
  "tap to interrupt" hint. Currently `allow_interruptions=False` — agent must finish
  before patient can speak.
- **Wakeword detection**: "Hey Swastha" to start listening after a period of silence.
- **Call recording playback**: Patient can request a replay of their booking details.
- **WhatsApp confirmation**: After booking, send WhatsApp message via Twilio.
  Backend webhook is already scaffolded — just needs frontend "Send to WhatsApp" button.

---

*This file is the single source of truth for the frontend. Do not contradict it in code.*

---

## Neomorphic Design System

> This section is the single source of truth for all visual styling.
> Tailwind utility classes alone cannot produce neomorphism — you MUST
> use custom CSS for the shadow system. Use Tailwind for layout and spacing
> only. All surface treatments come from the CSS variables defined below.

---

### What neomorphism is

Neomorphism makes UI elements look extruded from or pressed into the background.
Every surface is the same base color. Depth comes entirely from two shadows:
one light (top-left), one dark (bottom-right). No borders. No flat fills. No gradients.

Two states for every element:
- **Raised** (`neo-out`) — element pops out of the surface. Used for cards, inactive buttons.
- **Pressed** (`neo-in`) — element is pushed into the surface. Used for active states, input fields, transcript boxes.

---

### Base color palette

The entire UI lives on one background color. Everything derives from it.

```css
:root {
  /* Base — the background everything is extruded from */
  --neo-bg: #e8eaf0;

  /* The two shadow colors — always derived from --neo-bg */
  --neo-shadow-light: #ffffff;       /* lighter than bg by ~15% */
  --neo-shadow-dark:  #c5c8d2;       /* darker than bg by ~15% */

  /* Text */
  --neo-text:         #4a4f6a;       /* primary text — dark indigo-gray */
  --neo-text-muted:   #8b90a8;       /* secondary text */

  /* Accent — the only non-neutral color in the system */
  --neo-accent:       #6c72cb;       /* indigo-purple — used sparingly */
  --neo-accent-light: #eef0ff;       /* very pale accent for tints */

  /* Semantic — call states */
  --neo-green:        #5dba8a;       /* listening / connected / success */
  --neo-red:          #e07070;       /* end call / error / danger */
  --neo-amber:        #e0a870;       /* processing / warning */
}
```

**Dark mode:** Invert the shadow directions. Dark bg, lighter shadow on bottom-right, darker on top-left.

```css
@media (prefers-color-scheme: dark) {
  :root {
    --neo-bg:           #1e2130;
    --neo-shadow-light: #2a2f45;     /* slightly lighter than bg */
    --neo-shadow-dark:  #13161f;     /* slightly darker than bg */
    --neo-text:         #c8ccde;
    --neo-text-muted:   #6b7090;
    --neo-accent:       #8b91e0;
    --neo-accent-light: #2a2d4a;
    --neo-green:        #4aab7a;
    --neo-red:          #d06060;
    --neo-amber:        #c99860;
  }
}
```

---

### Shadow tokens

These are the only box-shadow values used in the entire app.
Never use any other shadow values.

```css
:root {
  /* Raised — element pops out of background */
  --neo-out-sm:  4px 4px 10px var(--neo-shadow-dark),
                -4px -4px 10px var(--neo-shadow-light);

  --neo-out-md:  6px 6px 14px var(--neo-shadow-dark),
                -6px -6px 14px var(--neo-shadow-light);

  --neo-out-lg:  10px 10px 24px var(--neo-shadow-dark),
                -10px -10px 24px var(--neo-shadow-light);

  /* Pressed — element pushed into background */
  --neo-in-sm:   inset 3px 3px 8px var(--neo-shadow-dark),
                 inset -3px -3px 8px var(--neo-shadow-light);

  --neo-in-md:   inset 4px 4px 10px var(--neo-shadow-dark),
                 inset -4px -4px 10px var(--neo-shadow-light);

  /* Pressed hard — active/clicked button state */
  --neo-in-hard: inset 5px 5px 12px var(--neo-shadow-dark),
                 inset -5px -5px 12px var(--neo-shadow-light);
}
```

---

### Border radius tokens

```css
:root {
  --neo-radius-sm:  10px;   /* small chips, badges, message bubbles */
  --neo-radius-md:  16px;   /* buttons, input fields, agent rows */
  --neo-radius-lg:  20px;   /* cards, panels */
  --neo-radius-xl:  28px;   /* the outer screen container */
  --neo-radius-full: 9999px; /* pills, circular buttons */
}
```

---

### Component specifications

#### Page background

```css
body {
  background: var(--neo-bg);
  min-height: 100vh;
  font-family: 'Noto Sans Devanagari', 'Segoe UI', system-ui, sans-serif;
  color: var(--neo-text);
}
```

#### Outer screen container (`.neo-screen`)

```css
.neo-screen {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-xl);
  padding: 1.5rem;
  box-shadow: var(--neo-out-lg);
  max-width: 680px;
  margin: 2rem auto;
}
```

#### Cards / panels (`.neo-card`)

```css
.neo-card {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-lg);
  padding: 1.25rem;
  box-shadow: var(--neo-out-md);
}
```

#### Inset surfaces — transcript, input fields (`.neo-inset`)

```css
.neo-inset {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-md);
  box-shadow: var(--neo-in-md);
  padding: 0.75rem 1rem;
}
```

#### Buttons — raised state (`.neo-btn`)

```css
.neo-btn {
  background: var(--neo-bg);
  border: none;
  border-radius: var(--neo-radius-md);
  box-shadow: var(--neo-out-sm);
  color: var(--neo-text);
  cursor: pointer;
  transition: box-shadow 0.15s ease, transform 0.1s ease;
}

.neo-btn:hover {
  box-shadow: var(--neo-out-md);
}

.neo-btn:active,
.neo-btn.pressed {
  box-shadow: var(--neo-in-hard);
  transform: scale(0.98);
}
```

#### Language selector pills (`.neo-lang-btn`)

```css
.neo-lang-btn {
  flex: 1;
  padding: 8px 4px;
  border-radius: var(--neo-radius-sm);
  border: none;
  background: var(--neo-bg);
  font-size: 12px;
  font-weight: 600;
  color: var(--neo-text-muted);
  cursor: pointer;
  box-shadow: var(--neo-out-sm);
  transition: all 0.15s;
}

/* Selected language */
.neo-lang-btn.active {
  box-shadow: var(--neo-in-md);
  color: var(--neo-accent);
}
```

#### Circular call buttons

```css
/* Start Call — green */
.neo-btn-call-start {
  width: 72px;
  height: 72px;
  border-radius: var(--neo-radius-full);
  background: var(--neo-green);
  border: none;
  cursor: pointer;
  box-shadow: var(--neo-out-md);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.neo-btn-call-start:active {
  box-shadow: var(--neo-in-hard);
  transform: scale(0.95);
}

/* End Call — red, smaller, always visible during call */
.neo-btn-call-end {
  width: 44px;
  height: 44px;
  border-radius: var(--neo-radius-full);
  background: var(--neo-red);
  border: none;
  cursor: pointer;
  box-shadow: var(--neo-out-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.neo-btn-call-end:active {
  box-shadow: var(--neo-in-hard);
  transform: scale(0.95);
}
```

#### Status badge (`.neo-status-badge`)

```css
.neo-status-badge {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-full);
  padding: 6px 14px;
  font-size: 11px;
  font-weight: 600;
  color: var(--neo-accent);
  box-shadow: var(--neo-in-sm);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

/* Animated dot inside badge */
.neo-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.neo-status-dot.listening  { background: var(--neo-green); animation: neo-blink 1.2s infinite; }
.neo-status-dot.processing { background: var(--neo-amber); animation: neo-blink 0.8s infinite; }
.neo-status-dot.speaking   { background: var(--neo-accent); animation: neo-blink 1s infinite; }

@keyframes neo-blink {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.25; }
}
```

#### Pulse ring — wraps the Start Call button during active listening

```css
.neo-pulse-ring {
  position: absolute;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  animation: neo-pulse 1.8s ease-out infinite;
  pointer-events: none;
}

@keyframes neo-pulse {
  0%   { box-shadow: 0 0 0 0 rgba(93, 186, 138, 0.5); }
  70%  { box-shadow: 0 0 0 20px rgba(93, 186, 138, 0); }
  100% { box-shadow: 0 0 0 0 rgba(93, 186, 138, 0); }
}
```

#### Sound wave animation — shown during listening state

```css
.neo-waves {
  display: flex;
  align-items: center;
  gap: 3px;
  height: 28px;
}

.neo-wave-bar {
  width: 3px;
  border-radius: 3px;
  background: var(--neo-accent);
  opacity: 0.7;
}

/* Vary heights — alternate short/tall bars */
.neo-wave-bar:nth-child(1) { height: 8px;  animation: neo-wave 1s ease-in-out infinite 0.0s; }
.neo-wave-bar:nth-child(2) { height: 18px; animation: neo-wave 1s ease-in-out infinite 0.1s; }
.neo-wave-bar:nth-child(3) { height: 24px; animation: neo-wave 1s ease-in-out infinite 0.2s; }
.neo-wave-bar:nth-child(4) { height: 18px; animation: neo-wave 1s ease-in-out infinite 0.3s; }
.neo-wave-bar:nth-child(5) { height: 10px; animation: neo-wave 1s ease-in-out infinite 0.4s; }
.neo-wave-bar:nth-child(6) { height: 20px; animation: neo-wave 1s ease-in-out infinite 0.15s; }
.neo-wave-bar:nth-child(7) { height: 14px; animation: neo-wave 1s ease-in-out infinite 0.25s; }

@keyframes neo-wave {
  0%, 100% { transform: scaleY(1); }
  50%       { transform: scaleY(0.35); }
}

/* Hide during non-listening states */
.neo-waves { display: none; }
.call-status-listening .neo-waves { display: flex; }
```

#### Agent activity rows

```css
.neo-agent-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 12px;
  transition: all 0.2s;
}

/* Completed agent — inset, green dot */
.neo-agent-row.done {
  background: var(--neo-bg);
  box-shadow: var(--neo-in-sm);
}

/* Currently active agent — inset, pulsing accent dot */
.neo-agent-row.active {
  background: var(--neo-bg);
  box-shadow: var(--neo-in-sm);
}

/* Not yet activated */
.neo-agent-row.pending {
  opacity: 0.4;
}

.neo-agent-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.neo-agent-dot.done    { background: var(--neo-green); }
.neo-agent-dot.active  { background: var(--neo-accent); animation: neo-blink 1s infinite; }
.neo-agent-dot.pending { background: var(--neo-shadow-dark); }

.neo-agent-name { font-size: 12px; font-weight: 600; color: var(--neo-text); }
.neo-agent-sub  { font-size: 10px; color: var(--neo-text-muted); margin-top: 1px; }
```

#### Transcript message bubbles

```css
.neo-transcript {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Agent bubble — raised, floats on the left */
.neo-msg-agent {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-sm);
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1.5;
  max-width: 85%;
  align-self: flex-start;
  box-shadow: var(--neo-out-sm);
  color: var(--neo-accent);
}

/* Patient bubble — accent-filled, right-aligned */
.neo-msg-patient {
  background: var(--neo-accent);
  border-radius: var(--neo-radius-sm);
  padding: 7px 10px;
  font-size: 12px;
  line-height: 1.5;
  max-width: 85%;
  align-self: flex-end;
  box-shadow: var(--neo-out-sm);
  color: #ffffff;
}

.neo-msg-label {
  font-size: 9px;
  font-weight: 700;
  opacity: 0.6;
  margin-bottom: 2px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
```

#### Booking confirmation card

```css
.neo-booking-card {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-lg);
  padding: 1rem 1.25rem;
  box-shadow: var(--neo-out-md);
  margin-top: 16px;
}

.neo-booking-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 0.75rem;
}

.neo-check-circle {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  background: var(--neo-green);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: var(--neo-out-sm);
  flex-shrink: 0;
}

.neo-booking-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.neo-booking-cell {
  background: var(--neo-bg);
  border-radius: var(--neo-radius-sm);
  padding: 8px 10px;
  box-shadow: var(--neo-in-sm);
}

.neo-booking-cell-label {
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--neo-text-muted);
  font-weight: 600;
}

.neo-booking-cell-val {
  font-size: 12px;
  font-weight: 600;
  color: var(--neo-text);
  margin-top: 2px;
}
```

---

### Typography

```css
/* Section labels above panels */
.neo-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--neo-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 0.75rem;
}

/* Primary text inside components */
.neo-text { font-size: 13px; color: var(--neo-text); }
.neo-text-sm { font-size: 11px; color: var(--neo-text); }
.neo-text-muted { font-size: 12px; color: var(--neo-text-muted); }

/* Devanagari script — applied to transcript and booking confirmation */
.neo-devanagari {
  font-family: 'Noto Sans Devanagari', system-ui, sans-serif;
  font-size: 13px;
  line-height: 1.6;
}
```

---

### Neomorphism rules — what NOT to do

These are the most common mistakes. Sonnet must avoid all of them:

1. **No borders** — borders break the extruded-from-surface illusion. Use shadows only.
2. **No flat colored backgrounds on cards** — every surface must be `var(--neo-bg)`. Only buttons with semantic meaning (Start/End Call) get color fills.
3. **No gradient fills** — neomorphism is flat color + shadow depth only.
4. **No sharp corners on interactive elements** — minimum `border-radius: 10px` on anything touchable.
5. **No multiple shadow depths on the same element** — pick either `neo-out` OR `neo-in`. Never combine them on the same element.
6. **No shadow on text** — text-shadow breaks the aesthetic. Color contrast alone carries readability.
7. **No colored borders as status indicators** — use the inset/raised shadow state change instead (active = neo-in, inactive = neo-out).
8. **The accent color (`--neo-accent`) is used sparingly** — active agent dots, active language button text, agent chat bubbles. Not for backgrounds, not for multiple elements simultaneously.
9. **Transitions on all interactive elements** — `transition: box-shadow 0.15s ease, transform 0.1s ease` on every button. The shadow transition IS the hover/active feedback.
10. **No card borders as separators** — use `neo-in` inset on the inner container instead of a border between sections.

---

### State → visual mapping (call lifecycle)

| Call state    | Status badge text | Status dot color | Waves shown | Pulse ring | Main btn style |
|---|---|---|---|---|---|
| `idle`        | —                 | —                | No          | No         | Start Call (green, neo-out-md) |
| `connecting`  | Connecting...     | amber, slow blink | No         | No         | Spinner overlay on btn |
| `greeting`    | Agent speaking... | accent, blink    | No          | No         | End Call visible (red, neo-out-sm) |
| `listening`   | Listening...      | green, blink     | Yes         | Yes        | End Call visible |
| `processing`  | Processing...     | amber, fast blink | No         | No         | End Call visible |
| `speaking`    | Agent speaking... | accent, blink    | No          | No         | End Call visible |
| `ending`      | Ending call...    | gray, no blink   | No          | No         | Disabled |
| `ended`       | —                 | —                | No          | No         | Start New Call |
| `call_dropped`| Call dropped      | red, no blink    | No          | No         | Reconnect (green) + End Session (red) |

---

### Tailwind + custom CSS coexistence

Use Tailwind for:
- Layout (`grid`, `flex`, `gap-*`, `p-*`, `m-*`, `w-*`, `h-*`)
- Responsive breakpoints (`md:grid-cols-2`)
- Text size and weight (`text-sm`, `font-semibold`)

Use custom CSS classes (defined in `globals.css`) for:
- All `box-shadow` values — must use the `--neo-*` variables
- All `background` on surface elements — always `var(--neo-bg)`
- All `border-radius` — use the `--neo-radius-*` tokens
- All `transition` on interactive elements
- All animations (`@keyframes`)

**Never mix Tailwind's `shadow-*` utilities with neomorphism.** Tailwind's shadows are directional and will clash with the symmetric neo shadow system. Purge all `shadow-*` Tailwind classes from the codebase.

Add to `tailwind.config.ts`:
```typescript
theme: {
  extend: {
    boxShadow: {
      none: 'none',  // keep this — used for reset
    }
  }
}
```

And disable Tailwind shadow utilities entirely in the preflight or just never use them.

---

### `globals.css` — full CSS to add to Next.js

```css
/* Import Devanagari font */
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400;500;600&display=swap');

:root {
  --neo-bg:           #e8eaf0;
  --neo-shadow-light: #ffffff;
  --neo-shadow-dark:  #c5c8d2;
  --neo-text:         #4a4f6a;
  --neo-text-muted:   #8b90a8;
  --neo-accent:       #6c72cb;
  --neo-accent-light: #eef0ff;
  --neo-green:        #5dba8a;
  --neo-red:          #e07070;
  --neo-amber:        #e0a870;
  --neo-out-sm:  4px 4px 10px var(--neo-shadow-dark), -4px -4px 10px var(--neo-shadow-light);
  --neo-out-md:  6px 6px 14px var(--neo-shadow-dark), -6px -6px 14px var(--neo-shadow-light);
  --neo-out-lg:  10px 10px 24px var(--neo-shadow-dark), -10px -10px 24px var(--neo-shadow-light);
  --neo-in-sm:   inset 3px 3px 8px var(--neo-shadow-dark), inset -3px -3px 8px var(--neo-shadow-light);
  --neo-in-md:   inset 4px 4px 10px var(--neo-shadow-dark), inset -4px -4px 10px var(--neo-shadow-light);
  --neo-in-hard: inset 5px 5px 12px var(--neo-shadow-dark), inset -5px -5px 12px var(--neo-shadow-light);
  --neo-radius-sm:   10px;
  --neo-radius-md:   16px;
  --neo-radius-lg:   20px;
  --neo-radius-xl:   28px;
  --neo-radius-full: 9999px;
}

@media (prefers-color-scheme: dark) {
  :root {
    --neo-bg:           #1e2130;
    --neo-shadow-light: #2a2f45;
    --neo-shadow-dark:  #13161f;
    --neo-text:         #c8ccde;
    --neo-text-muted:   #6b7090;
    --neo-accent:       #8b91e0;
    --neo-accent-light: #2a2d4a;
    --neo-green:        #4aab7a;
    --neo-red:          #d06060;
    --neo-amber:        #c99860;
  }
}

body {
  background: var(--neo-bg);
  color: var(--neo-text);
  font-family: 'Noto Sans Devanagari', 'Segoe UI', system-ui, sans-serif;
}

/* All component classes from this design system */
.neo-screen    { background: var(--neo-bg); border-radius: var(--neo-radius-xl); box-shadow: var(--neo-out-lg); }
.neo-card      { background: var(--neo-bg); border-radius: var(--neo-radius-lg); box-shadow: var(--neo-out-md); }
.neo-inset     { background: var(--neo-bg); border-radius: var(--neo-radius-md); box-shadow: var(--neo-in-md); }
.neo-btn       { background: var(--neo-bg); border: none; border-radius: var(--neo-radius-md); box-shadow: var(--neo-out-sm); cursor: pointer; transition: box-shadow 0.15s ease, transform 0.1s ease; }
.neo-btn:hover { box-shadow: var(--neo-out-md); }
.neo-btn:active, .neo-btn.pressed { box-shadow: var(--neo-in-hard); transform: scale(0.98); }
.neo-label     { font-size: 10px; font-weight: 600; color: var(--neo-text-muted); text-transform: uppercase; letter-spacing: 0.08em; }

@keyframes neo-blink { 0%,100%{opacity:1} 50%{opacity:.25} }
@keyframes neo-pulse { 0%{box-shadow:0 0 0 0 rgba(93,186,138,.5)} 70%{box-shadow:0 0 0 20px rgba(93,186,138,0)} 100%{box-shadow:0 0 0 0 rgba(93,186,138,0)} }
@keyframes neo-wave  { 0%,100%{transform:scaleY(1)} 50%{transform:scaleY(.35)} }
```

---

*This design system section is the single source of truth for all visual styling.
Do not use Tailwind shadow utilities. Do not add borders to surfaces.
Do not use any color other than --neo-bg as a card background.*