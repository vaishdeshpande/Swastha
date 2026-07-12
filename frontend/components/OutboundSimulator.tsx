"use client";

import { useEffect, useRef, useState } from "react";
import { TranscriptMessage, LangOption } from "@/lib/types";
import TranscriptPanel from "@/components/TranscriptPanel";
import LanguageSelector from "@/components/LanguageSelector";

// ── Outbound-specific agent order ──
const OUTBOUND_AGENTS = [
  { key: "route_job",             label: "Job Router",            sublabel: "Routing outbound job" },
  { key: "scheduler_outbound",    label: "Appointment Confirmer", sublabel: "Confirming appointment" },
  { key: "prescription_outbound", label: "Rx Reminder Agent",     sublabel: "Sending medication reminder" },
  { key: "followup_outbound",     label: "Follow-up Agent",       sublabel: "Post-discharge check-in" },
  { key: "escalate",              label: "Escalation",            sublabel: "High-risk alert fired" },
] as const;

function OutboundAgentFeed({ activeAgent, completedAgents }: { activeAgent: string | null; completedAgents: string[] }) {
  return (
    <div className="neo-card">
      <p className="neo-label">Agent Activity</p>
      <div className="flex flex-col gap-1">
        {OUTBOUND_AGENTS.map(({ key, label, sublabel }) => {
          const isActive = activeAgent === key;
          const isDone = completedAgents.includes(key) && !isActive;
          const state = isActive ? "active" : isDone ? "done" : "pending";
          return (
            <div key={key} className={`neo-agent-row ${state}`}>
              <span className={`neo-agent-dot ${state}`} />
              <div>
                <div className="neo-agent-name">{label}</div>
                {isActive && <div className="neo-agent-sub">{sublabel}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Job-specific sub-sections ──
function DischargeSection({ discharge: d }: { discharge: Record<string, unknown> }) {
  const rawMeds = (d.medications as Array<string | Record<string,string>>) || [];
  const medNames = rawMeds.map(m => typeof m === "string" ? m : (m.name ?? JSON.stringify(m)));
  return (
    <div>
      <p style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)", margin: "0 0 6px" }}>Discharge Info</p>
      <div className="neo-inset" style={{ padding: "8px 12px" }}>
        <InfoRow label="Diagnosis" value={String(d.diagnosis ?? "—")} />
        <InfoRow label="Discharged" value={String(d.discharge_date ?? "—")} />
        <InfoRow label="Follow-up due" value={String(d.follow_up_due ?? "—")} />
        {medNames.length > 0 && (
          <div style={{ paddingTop: 5, fontSize: 11, color: "var(--neo-text-muted)" }}>
            <span style={{ fontWeight: 700 }}>Meds: </span>{medNames.join(", ")}
          </div>
        )}
      </div>
    </div>
  );
}

function AppointmentSection({ appointment: a }: { appointment: Record<string, unknown> }) {
  return (
    <div>
      <p style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)", margin: "0 0 6px" }}>Appointment</p>
      <div className="neo-inset" style={{ padding: "8px 12px" }}>
        <InfoRow label="Doctor" value={String(a.doctor_name ?? "—")} />
        <InfoRow label="Department" value={String(a.department ?? "—")} valueStyle={{ textTransform: "capitalize" }} />
        <InfoRow label="Date" value={String(a.date ?? "—")} />
        <InfoRow label="Time" value={String(a.time ?? "—")} valueStyle={{ color: "var(--neo-accent)", fontWeight: 700 }} />
      </div>
    </div>
  );
}

function PrescriptionSection({ prescription: p }: { prescription: Record<string, unknown> }) {
  const meds = (p.medicines as Array<{ name: string; dosage: string; frequency: string }>) || [];
  return (
    <div>
      <p style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)", margin: "0 0 6px" }}>Prescription</p>
      <div className="neo-inset" style={{ padding: "8px 12px", display: "flex", flexDirection: "column", gap: 8 }}>
        <InfoRow label="Prescribed by" value={String(p.doctor_name ?? "—")} />
        {meds.map((m, i) => (
          <div key={i} style={{ paddingLeft: 8, borderLeft: "2px solid var(--neo-accent)" }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: "var(--neo-text)" }}>{m.name} <span style={{ color: "var(--neo-accent)" }}>{m.dosage}</span></div>
            <div style={{ fontSize: 11, color: "var(--neo-text-muted)", marginTop: 1 }}>{m.frequency}</div>
          </div>
        ))}
        {p.notes_en != null && <div style={{ fontSize: 11, color: "var(--neo-text-muted)", fontStyle: "italic", paddingTop: 2 }}>{String(p.notes_en)}</div>}
      </div>
    </div>
  );
}

// ── Reusable inline row ──
function InfoRow({ label, value, valueStyle }: { label: string; value: string; valueStyle?: React.CSSProperties }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", padding: "5px 0", borderBottom: "1px solid var(--neo-accent-light)" }}>
      <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)" }}>{label}</span>
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--neo-text)", textAlign: "right", maxWidth: "60%", ...valueStyle }}>{value}</span>
    </div>
  );
}

// ── Patient context left panel ──
function PatientContextCard({ info, jobType }: { info: Record<string, unknown>; jobType: string }) {
  const history = (info.medical_history as Array<{ condition: string; year: number }>) || [];

  return (
    <div className="neo-card" style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="neo-label" style={{ margin: 0 }}>Patient Context</p>

      {/* Identity rows */}
      <div className="neo-inset" style={{ padding: "8px 12px", display: "flex", flexDirection: "column" }}>
        <InfoRow label="Name" value={String(info.name ?? "—")} valueStyle={{ fontWeight: 700, fontSize: 13 }} />
        <InfoRow label="Age" value={`${info.age ?? "—"} yrs`} />
        <InfoRow label="Phone" value={String(info.phone ?? "—")} valueStyle={{ fontFamily: "monospace", fontSize: 11 }} />
        {info.blood_group != null && (
          <InfoRow label="Blood Group" value={String(info.blood_group)} valueStyle={{ color: "var(--neo-red)", fontWeight: 700 }} />
        )}
      </div>

      {/* Medical history */}
      {history.length > 0 && (
        <div>
          <p style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)", margin: "0 0 6px" }}>Medical History</p>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {history.map((h, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--neo-text)" }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--neo-amber)", flexShrink: 0 }} />
                <span style={{ textTransform: "capitalize" }}>{h.condition}</span>
                <span style={{ color: "var(--neo-text-muted)", marginLeft: "auto" }}>{h.year}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Job-specific context */}
      {jobType === "followup" && info.discharge != null && <DischargeSection discharge={info.discharge as Record<string, unknown>} />}
      {jobType === "confirmation" && info.appointment != null && <AppointmentSection appointment={info.appointment as Record<string, unknown>} />}
      {jobType === "rx_reminder" && info.prescription != null && <PrescriptionSection prescription={info.prescription as Record<string, unknown>} />}
    </div>
  );
}

// ── Outcome card ──
function OutcomeCard({ outcome, jobType }: { outcome: Record<string, unknown>; jobType: string }) {
  const riskRaw = outcome.readmission_risk as number | undefined;
  const riskColor = riskRaw === undefined ? "var(--neo-text-muted)"
    : riskRaw > 0.7 ? "var(--neo-red)"
    : riskRaw > 0.4 ? "var(--neo-amber)"
    : "var(--neo-green)";

  return (
    <div className="neo-card" style={{ padding: "1rem" }}>
      <p className="neo-label" style={{ margin: "0 0 10px" }}>Call Outcome</p>
      <div className="neo-inset" style={{ padding: "8px 12px", display: "flex", flexDirection: "column" }}>
        <InfoRow
          label="Status"
          value={String(outcome.status ?? "—")}
          valueStyle={{ color: outcome.status === "completed" ? "var(--neo-green)" : "var(--neo-red)", textTransform: "capitalize" }}
        />
        {jobType === "followup" && riskRaw !== undefined && (
          <>
            <div style={{ padding: "6px 0", borderBottom: "1px solid var(--neo-accent-light)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 5 }}>
                <span style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--neo-text-muted)" }}>Readmission Risk</span>
                <span style={{ fontSize: 12, fontWeight: 700, color: riskColor }}>{Math.round(riskRaw * 100)}%</span>
              </div>
              <div style={{ height: 5, borderRadius: 3, background: "var(--neo-accent-light)", overflow: "hidden" }}>
                <div style={{ width: `${Math.round(riskRaw * 100)}%`, height: "100%", background: riskColor, borderRadius: 3, transition: "width 0.8s ease" }} />
              </div>
            </div>
            <InfoRow label="Fever" value={outcome.fever ? "Yes" : "No"} valueStyle={{ color: outcome.fever ? "var(--neo-red)" : "var(--neo-green)" }} />
            <InfoRow label="Pain Level" value={`${outcome.pain_level} / 10`} />
            <InfoRow label="Medication" value={String(outcome.medication_adherence ?? "—")} valueStyle={{ textTransform: "capitalize" }} />
          </>
        )}
        {jobType === "confirmation" && (
          <InfoRow label="Confirmed" value={outcome.confirmed ? "Yes ✓" : "No"} valueStyle={{ color: outcome.confirmed ? "var(--neo-green)" : "var(--neo-text-muted)" }} />
        )}
        {jobType === "rx_reminder" && (
          <InfoRow label="Reminder Sent" value={outcome.reminder_sent ? "Yes ✓" : "No"} valueStyle={{ color: outcome.reminder_sent ? "var(--neo-green)" : "var(--neo-text-muted)" }} />
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

const JOB_LABELS: Record<string, string> = {
  followup:     "Follow-up Call",
  confirmation: "Appointment Reminder",
  rx_reminder:  "Prescription Reminder",
};

// ── Browser TTS ──
function speak(text: string, lang: string): Promise<void> {
  return new Promise((resolve) => {
    if (typeof window === "undefined" || !window.speechSynthesis) { resolve(); return; }
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = lang;
    utt.rate = 0.92;
    utt.onend = () => resolve();
    utt.onerror = () => resolve();
    window.speechSynthesis.speak(utt);
  });
}

// ── Browser STT ──
type SpeechRecognitionInstance = {
  lang: string; continuous: boolean; interimResults: boolean;
  onresult: ((e: SpeechRecognitionEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
};
type SpeechRecognitionEvent = { results: { [i: number]: { [j: number]: { transcript: string } } } };

function createRecognition(lang: string): SpeechRecognitionInstance | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as Record<string, unknown>;
  const SR = (w.SpeechRecognition ?? w.webkitSpeechRecognition) as (new () => SpeechRecognitionInstance) | undefined;
  if (!SR) return null;
  const rec = new SR() as SpeechRecognitionInstance;
  rec.lang = lang;
  rec.continuous = false;
  rec.interimResults = false;
  return rec;
}

type SimState = "loading" | "agent_speaking" | "listening" | "processing" | "done" | "error";

interface Props {
  jobType: "followup" | "confirmation" | "rx_reminder";
  onClose: () => void;
}

export default function OutboundSimulator({ jobType, onClose }: Props) {
  const [simState, setSimState]               = useState<SimState>("loading");
  const [langCode, setLangCode]               = useState<LangOption>("hi-IN");
  const [sessionId, setSessionId]             = useState<string | null>(null);
  const [patientInfo, setPatientInfo]         = useState<Record<string, unknown> | null>(null);
  const [transcript, setTranscript]           = useState<TranscriptMessage[]>([]);
  const [activeAgent, setActiveAgent]         = useState<string | null>(null);
  const [completedAgents, setCompletedAgents] = useState<string[]>([]);
  const [callOutcome, setCallOutcome]         = useState<Record<string, unknown> | null>(null);
  const [error, setError]                     = useState<string | null>(null);
  const [interimText, setInterimText]         = useState("");   // live STT preview
  const [hasMic, setHasMic]                   = useState(true); // false if browser has no STT
  const [userInput, setUserInput]             = useState("");   // fallback text input
  const inputRef    = useRef<HTMLInputElement>(null);
  const recRef      = useRef<SpeechRecognitionInstance | null>(null);
  const sessionRef  = useRef<string | null>(null);             // stable ref for async callbacks
  const startedRef  = useRef(false);                           // StrictMode double-invoke guard

  const resolvedLang = langCode === "auto" ? "hi-IN" : (langCode as string);

  // ── Speak agent message, then start listening ──
  async function deliverAgentMessage(text: string, agent: string, done: boolean, outcome: Record<string,unknown> | null) {
    setActiveAgent(agent);

    // Backend returned an empty reply (LLM glitch) — never render an empty
    // bubble; recover straight back to listening so the patient can repeat.
    if (!text.trim()) {
      if (done) {
        if (outcome) setCallOutcome(outcome);
        setSimState("done");
      } else {
        setSimState("listening");
      }
      return;
    }

    setTranscript(prev => [...prev, { role: "assistant", content: text, timestamp: Date.now() }]);
    setSimState("agent_speaking");
    await speak(text, resolvedLang);

    if (done) {
      setActiveAgent(prev => { if (prev) setCompletedAgents(c => c.includes(prev) ? c : [...c, prev]); return null; });
      if (outcome) setCallOutcome(outcome);
      setSimState("done");
    } else {
      // Wait for speaker audio to fully clear before opening mic — prevents TTS echo
      await new Promise(r => setTimeout(r, 800));
      setSimState("listening");
    }
  }

  // ── Send patient reply to backend, get next agent message ──
  async function sendReply(msg: string) {
    const sid = sessionRef.current;
    if (!msg.trim() || !sid) return;

    setInterimText("");
    setUserInput("");
    setTranscript(prev => [...prev, { role: "user", content: msg.trim(), timestamp: Date.now() }]);
    setSimState("processing");

    try {
      const res = await fetch(`/api/demo/outbound/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sid, message: msg.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const data: { agent_message: string; current_agent: string; call_outcome: Record<string,unknown> | null; done: boolean } = await res.json();

      setActiveAgent(prev => {
        if (prev && prev !== data.current_agent) setCompletedAgents(c => c.includes(prev) ? c : [...c, prev]);
        return data.current_agent;
      });

      await deliverAgentMessage(data.agent_message, data.current_agent, data.done, data.call_outcome);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSimState("error");
    }
  }

  // ── Start mic listening ──
  function startListening() {
    const rec = createRecognition(resolvedLang);
    if (!rec) { setHasMic(false); setSimState("listening"); return; }
    recRef.current?.abort();
    recRef.current = rec;

    rec.onresult = (e) => {
      const txt = e.results[0][0].transcript;
      setInterimText(txt);
    };
    rec.onerror = () => { recRef.current = null; setSimState("listening"); };
    rec.onend = () => {
      const txt = interimTextRef.current;
      recRef.current = null;
      if (txt) sendReply(txt);
      else setSimState("listening");
    };
    rec.start();
  }

  // Need a stable ref for interimText so rec.onend closure can read it
  const interimTextRef = useRef("");
  useEffect(() => { interimTextRef.current = interimText; }, [interimText]);

  // ── Start simulation ──
  async function startSimulation() {
    window.speechSynthesis?.cancel();
    recRef.current?.abort();
    recRef.current = null;
    setSimState("loading");
    setTranscript([]);
    setActiveAgent(null);
    setCompletedAgents([]);
    setCallOutcome(null);
    setError(null);
    setSessionId(null);
    setPatientInfo(null);
    setInterimText("");
    setUserInput("");

    try {
      const res = await fetch(`/api/demo/outbound/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_type: jobType, lang_code: resolvedLang }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const data: { session_id: string; patient_info: Record<string,unknown>; agent_message: string; current_agent: string } = await res.json();

      sessionRef.current = data.session_id;
      setSessionId(data.session_id);
      setPatientInfo(data.patient_info);
      await deliverAgentMessage(data.agent_message, data.current_agent, false, null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSimState("error");
    }
  }

  // Auto-start on mount; cancel speech on unmount
  useEffect(() => {
    if (startedRef.current) return;   // StrictMode fires this twice — only run once
    startedRef.current = true;
    startSimulation();
    return () => { window.speechSynthesis?.cancel(); recRef.current?.abort(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // When state becomes "listening", auto-start mic
  useEffect(() => {
    if (simState === "listening" && hasMic) startListening();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [simState]);

  // Fallback text submit
  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendReply(userInput); }
  }

  const statusLabel: Record<SimState, string> = {
    loading:        "Connecting…",
    agent_speaking: "Agent speaking…",
    listening:      hasMic ? "Listening — speak now" : "Type your reply below ↓",
    processing:     "Agent is responding…",
    done:           "Call ended — simulation complete",
    error:          `Error: ${error}`,
  };

  const statusDot: Record<SimState, string> = {
    loading:        "processing",
    agent_speaking: "speaking",
    listening:      "listening",
    processing:     "processing",
    done:           "",
    error:          "dropped",
  };

  return (
    <div style={{ position: "fixed", inset: 0, zIndex: 50, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", padding: "1rem" }}>
      <div className="neo-screen" style={{ width: "min(980px, 96vw)", maxHeight: "92vh", display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* ── Header ── */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 20, flexShrink: 0 }}>
          <span style={{ fontSize: 11, fontWeight: 700, background: "var(--neo-accent-light)", color: "var(--neo-accent)", padding: "3px 8px", borderRadius: 4, textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Outbound
          </span>
          <span style={{ fontSize: 15, fontWeight: 700, color: "var(--neo-text)" }}>{JOB_LABELS[jobType]}</span>

          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <LanguageSelector value={langCode} onChange={v => setLangCode(v)} disabled={simState !== "loading" && simState !== "error"} />
            {(simState === "done" || simState === "error") && (
              <button type="button" className="neo-btn" style={{ padding: "6px 14px", fontSize: 12, color: "var(--neo-accent)", fontWeight: 700 }} onClick={startSimulation}>
                ↺ Re-run
              </button>
            )}
            <button type="button" className="neo-btn" style={{ padding: "6px 14px", fontSize: 13, fontWeight: 700 }} onClick={() => { window.speechSynthesis?.cancel(); recRef.current?.abort(); onClose(); }} aria-label="Close">✕</button>
          </div>
        </div>

        {/* ── Status bar ── */}
        <div style={{ marginBottom: 16, display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <span className={`neo-status-dot ${statusDot[simState]}`} />
          <span className="neo-text-muted">{statusLabel[simState]}</span>
        </div>

        {/* ── Main body ── */}
        <div style={{ display: "grid", gridTemplateColumns: "280px 1fr", gap: 20, flex: 1, minHeight: 0 }}>

          {/* Left column — Patient Context, Voice Bar, Outcome */}
          <div style={{ display: "flex", flexDirection: "column", gap: 14, overflowY: "auto", paddingRight: 2 }}>
            {patientInfo && <PatientContextCard info={patientInfo} jobType={jobType} />}

            {/* Voice status bar — in left column */}
            {(simState === "listening" || simState === "agent_speaking" || simState === "processing") && (
              <div style={{
                borderRadius: 14,
                background: "var(--neo-accent-light)",
                border: "1.5px solid var(--neo-accent)",
                padding: "14px 16px",
                display: "flex",
                gap: 12,
                alignItems: "center",
              }}>
                <div style={{
                  width: 38, height: 38, borderRadius: "50%", flexShrink: 0,
                  background: simState === "listening" ? "var(--neo-accent)" : "var(--neo-bg)",
                  border: "2px solid var(--neo-accent)",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 16,
                  boxShadow: simState === "listening" ? "0 0 0 4px rgba(47,125,107,0.2)" : "none",
                  transition: "all 0.25s",
                }}>
                  {simState === "agent_speaking"
                    ? <span>🔊</span>
                    : simState === "processing"
                    ? <span>⏳</span>
                    : <span style={{ filter: "brightness(10)" }}>🎤</span>}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 10, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.07em", color: "var(--neo-accent)", marginBottom: 2 }}>
                    {simState === "listening" ? "Listening" : simState === "agent_speaking" ? "Agent Speaking" : "Processing"}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--neo-text-muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                    {interimText
                      ? <span style={{ fontStyle: "italic", color: "var(--neo-text)" }}>{interimText}</span>
                      : simState === "listening"
                      ? (resolvedLang === "hi-IN" ? "बोलिए… माइक सुन रहा है" : "बोला… मायक्रोफोन ऐकत आहे")
                      : simState === "agent_speaking"
                      ? (resolvedLang === "hi-IN" ? "एजेंट बोल रहा है…" : "एजेंट बोलत आहे…")
                      : (resolvedLang === "hi-IN" ? "प्रतीक्षा करें…" : "थांबा…")}
                  </div>
                </div>
              </div>
            )}

            {callOutcome && <OutcomeCard outcome={callOutcome} jobType={jobType} />}
          </div>

          {/* Right column: transcript only */}
          <div style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
            <div style={{ flex: 1, minHeight: 0 }}>
              <TranscriptPanel messages={transcript} langCode={resolvedLang} />
            </div>

            {/* Fallback text input when no mic */}
            {!hasMic && simState === "listening" && (
              <div className="neo-inset" style={{ marginTop: 12, flexShrink: 0, padding: "10px 14px", display: "flex", gap: 10, alignItems: "center" }}>
                <input
                  ref={inputRef}
                  type="text"
                  value={userInput}
                  onChange={e => setUserInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={resolvedLang === "hi-IN" ? "यहाँ टाइप करें…" : "येथे टाइप करा…"}
                  style={{ flex: 1, background: "none", border: "none", outline: "none", fontSize: 13, color: "var(--neo-text)", fontFamily: "inherit" }}
                  autoFocus
                />
                <button type="button" className="neo-btn" disabled={!userInput.trim()} onClick={() => sendReply(userInput)}
                  style={{ padding: "6px 16px", fontSize: 12, color: "var(--neo-accent)", fontWeight: 700 }}>
                  Send ↵
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
