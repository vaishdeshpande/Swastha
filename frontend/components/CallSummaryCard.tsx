"use client";

import { CallSummary } from "@/lib/types";

interface Props {
  summary: CallSummary;
  onStartNewCall: () => void;
  onReconnect?: () => void;
}

const LANG_LABEL: Record<string, string> = {
  "hi-IN": "Hindi",
  "mr-IN": "Marathi",
  auto: "Auto",
};

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export default function CallSummaryCard({ summary, onStartNewCall, onReconnect }: Props) {
  if (summary.dropped) {
    return (
      <div style={{ width: "100%", display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
        <div style={{ width: 14, height: 14, borderRadius: "50%", background: "var(--neo-red)" }} />
        <p style={{ fontSize: 13, fontWeight: 700, color: "var(--neo-red)" }}>
          Call dropped unexpectedly
        </p>
        <div style={{ display: "flex", gap: 10, width: "100%", marginTop: 8 }}>
          {onReconnect && (
            <button
              type="button"
              onClick={onReconnect}
              style={{
                flex: 1, padding: "10px", borderRadius: 12, border: "none",
                background: "var(--neo-green)", color: "#ffffff",
                fontSize: 12, fontWeight: 700, cursor: "pointer",
                boxShadow: "4px 4px 10px var(--neo-shadow-dark), -4px -4px 10px var(--neo-shadow-light)",
                fontFamily: "inherit",
              }}
            >
              Reconnect
            </button>
          )}
          <button
            type="button"
            onClick={onStartNewCall}
            style={{
              flex: 1, padding: "10px", borderRadius: 12, border: "none",
              background: "var(--neo-bg)", color: "var(--neo-text)",
              fontSize: 12, fontWeight: 700, cursor: "pointer",
              boxShadow: "4px 4px 10px var(--neo-shadow-dark), -4px -4px 10px var(--neo-shadow-light)",
              fontFamily: "inherit",
            }}
          >
            End Session
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={{ width: "100%" }}>
      <p style={{ fontSize: 14, fontWeight: 800, color: "var(--neo-text)", marginBottom: 14, textAlign: "left" }}>
        Call Summary
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, textAlign: "left" }}>
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Duration</div>
          <div className="neo-booking-cell-val">{formatDuration(summary.durationSec)}</div>
        </div>
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Language</div>
          <div className="neo-booking-cell-val">{LANG_LABEL[summary.lang] ?? summary.lang}</div>
        </div>
        {summary.intent && (
          <div className="neo-booking-cell">
            <div className="neo-booking-cell-label">Intent</div>
            <div className="neo-booking-cell-val">{summary.intent}</div>
          </div>
        )}
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Agents Used</div>
          <div className="neo-booking-cell-val">{summary.agentsUsed.join(" → ") || "—"}</div>
        </div>
      </div>
      <button
        type="button"
        onClick={onStartNewCall}
        style={{
          width: "100%", marginTop: 16, padding: "12px",
          borderRadius: 14, border: "none",
          background: "var(--neo-bg)",
          boxShadow: "4px 4px 10px var(--neo-shadow-dark), -4px -4px 10px var(--neo-shadow-light)",
          fontSize: 13, fontWeight: 700, color: "var(--neo-accent)",
          cursor: "pointer", fontFamily: "inherit",
        }}
      >
        Start New Call
      </button>
    </div>
  );
}
