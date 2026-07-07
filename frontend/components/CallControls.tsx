"use client";

import LanguageSelector from "@/components/LanguageSelector";
import { CallStatus, LangOption } from "@/lib/types";

interface CallControlsProps {
  callStatus: CallStatus;
  preferredLang: LangOption;
  onLangChange: (lang: LangOption) => void;
  onStartCall: () => void;
  onEndCall: () => void;
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
}: CallControlsProps) {
  const isPreCall = callStatus === "idle";
  const isInCall = !isPreCall && callStatus !== "ended" && callStatus !== "call_dropped";

  if (isPreCall) {
    return (
      <div className="flex flex-col items-center gap-6">
        <LanguageSelector value={preferredLang} onChange={onLangChange} />
        <button type="button" className="neo-btn-call-start" onClick={onStartCall} aria-label="Start Call">
          <PhoneIcon />
        </button>
        <p className="neo-text-muted">Start Call</p>
      </div>
    );
  }

  if (isInCall) {
    const showWaves = callStatus === "listening";
    return (
      <div className="flex flex-col items-center gap-4">
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
        >
          <PhoneOffIcon />
        </button>
      </div>
    );
  }

  return null;
}

function PhoneIcon() {
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />
    </svg>
  );
}

function PhoneOffIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2">
      <line x1="23" y1="1" x2="1" y2="23" />
      <path d="M10.68 13.31a16 16 0 0 0 3.41 2.6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7a2 2 0 0 1 1.72 2v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.42 19.42 0 0 1-3.33-2.67m-2.67-3.34a19.79 19.79 0 0 1-3.07-8.63A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91" />
    </svg>
  );
}
