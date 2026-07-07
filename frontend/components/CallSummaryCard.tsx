"use client";

import { CallSummary } from "@/lib/types";

interface Props {
  summary: CallSummary;
  onStartNewCall: () => void;
  onReconnect?: () => void;
}

function formatDuration(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export default function CallSummaryCard({ summary, onStartNewCall, onReconnect }: Props) {
  if (summary.dropped) {
    return (
      <div className="neo-card flex flex-col items-center gap-4 text-center">
        <p className="neo-text" style={{ color: "var(--neo-red)", fontWeight: 600 }}>
          ⚠ Call dropped unexpectedly
        </p>
        {summary.dropReason && <p className="neo-text-muted">{summary.dropReason}</p>}
        <div className="flex gap-3">
          {onReconnect && (
            <button type="button" className="neo-btn px-4 py-2 text-sm" style={{ color: "var(--neo-green)" }} onClick={onReconnect}>
              Reconnect
            </button>
          )}
          <button type="button" className="neo-btn px-4 py-2 text-sm" style={{ color: "var(--neo-red)" }} onClick={onStartNewCall}>
            End Session
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="neo-card flex flex-col gap-3">
      <p className="neo-label" style={{ marginBottom: 0 }}>
        Call Summary
      </p>
      <div className="neo-booking-grid">
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Duration</div>
          <div className="neo-booking-cell-val">{formatDuration(summary.durationSec)}</div>
        </div>
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Language</div>
          <div className="neo-booking-cell-val">{summary.lang}</div>
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
      <button type="button" className="neo-btn px-4 py-2 text-sm self-start" onClick={onStartNewCall}>
        Start New Call
      </button>
    </div>
  );
}
