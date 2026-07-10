"use client";

import LanguageSelector from "@/components/LanguageSelector";
import { CallStatus, LangOption } from "@/lib/types";

interface CallControlsProps {
  callStatus: CallStatus;
  preferredLang: LangOption;
  onLangChange: (lang: LangOption) => void;
  onStartCall: () => void;
  onEndCall: () => void;
  onSimulateDrop?: () => void;
}

const STATUS_TEXT: Partial<Record<CallStatus, string>> = {
  connecting: "Connecting...",
  greeting: "Agent speaking...",
  listening: "Listening...",
  processing: "Processing...",
  speaking: "Agent speaking...",
  ending: "Ending call...",
  call_dropped: "Call dropped",
};

const STATUS_DOT_CLASS: Partial<Record<CallStatus, string>> = {
  connecting: "processing",
  greeting: "speaking",
  listening: "listening",
  processing: "processing",
  speaking: "speaking",
  call_dropped: "dropped",
};

export default function CallControls({
  callStatus,
  preferredLang,
  onLangChange,
  onStartCall,
  onEndCall,
  onSimulateDrop,
}: CallControlsProps) {
  const isPreCall = callStatus === "idle";
  const isInCall = !isPreCall && callStatus !== "ended" && callStatus !== "call_dropped";

  if (isPreCall) {
    return (
      <>
        <p className="neo-label" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
          Select Language
        </p>
        <LanguageSelector value={preferredLang} onChange={onLangChange} />
        <button
          type="button"
          className="neo-btn-call-start"
          onClick={onStartCall}
          aria-label="Start Call"
          style={{ marginTop: 6 }}
        >
          <PlayIcon />
        </button>
        <p className="neo-text" style={{ fontSize: 13, fontWeight: 700 }}>Start Call</p>
      </>
    );
  }

  if (isInCall) {
    const showWaves = callStatus === "listening";
    return (
      <>
        <div className="neo-status-badge">
          <span className={`neo-status-dot ${STATUS_DOT_CLASS[callStatus] ?? ""}`} />
          {STATUS_TEXT[callStatus]}
        </div>
        <div className={`neo-waves ${showWaves ? "active" : ""}`}>
          {Array.from({ length: 7 }).map((_, i) => (
            <span key={i} className="neo-wave-bar" />
          ))}
        </div>
        <button
          type="button"
          className="neo-btn-call-end"
          onClick={onEndCall}
          disabled={callStatus === "ending"}
          aria-label="End Call"
          style={{ marginTop: 6 }}
        >
          <StopIcon />
        </button>
        <p className="neo-text" style={{ fontSize: 13, fontWeight: 700 }}>End Call</p>
        {onSimulateDrop && (
          <button
            type="button"
            onClick={onSimulateDrop}
            style={{
              background: "none", border: "none", color: "var(--neo-text-muted)",
              fontSize: 10, cursor: "pointer", textDecoration: "underline",
              marginTop: 0, fontFamily: "inherit",
            }}
          >
            simulate dropped call
          </button>
        )}
      </>
    );
  }

  return null;
}

function PlayIcon() {
  return (
    <div style={{
      width: 0, height: 0,
      borderTop: "11px solid transparent",
      borderBottom: "11px solid transparent",
      borderLeft: "18px solid #ffffff",
      marginLeft: 4,
    }} />
  );
}

function StopIcon() {
  return (
    <div style={{
      width: 16, height: 16,
      borderRadius: 3,
      background: "#ffffff",
    }} />
  );
}
