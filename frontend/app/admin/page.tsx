"use client";

import { useState } from "react";
import OutboundSimulator from "@/components/OutboundSimulator";

type JobType = "followup" | "confirmation" | "rx_reminder";

const JOB_OPTIONS: { label: string; value: JobType; icon: string; desc: string }[] = [
  {
    label: "Follow-up Call",
    value: "followup",
    icon: "🏥",
    desc: "Post-discharge health check-in",
  },
  {
    label: "Prescription Reminder",
    value: "rx_reminder",
    icon: "💊",
    desc: "Medication adherence reminder",
  },
];

export default function AdminPage() {
  const [activeJob, setActiveJob] = useState<JobType | null>(null);

  return (
    <main style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "2rem 1rem" }}>
      <div className="neo-screen" style={{ maxWidth: 700, width: "100%" }}>

        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: "2.5rem" }}>
          <span style={{ fontSize: 22 }}>📞</span>
          <h1 style={{ fontSize: 18, fontWeight: 700, color: "var(--neo-text)" }}>
            Outbound Call Simulator
          </h1>
          <a
            href="/"
            className="neo-btn"
            style={{ marginLeft: "auto", padding: "6px 14px", fontSize: 12, color: "var(--neo-text-muted)", textDecoration: "none" }}
          >
            ← Back to App
          </a>
        </div>

        {/* Big call buttons */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {JOB_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setActiveJob(opt.value)}
              style={{
                background: "var(--neo-card)",
                border: "1.5px solid var(--neo-border)",
                borderRadius: 14,
                padding: "20px 24px",
                display: "flex",
                alignItems: "center",
                gap: 18,
                cursor: "pointer",
                textAlign: "left",
                fontFamily: "inherit",
                transition: "border-color 0.18s, box-shadow 0.18s",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--neo-accent)";
                (e.currentTarget as HTMLButtonElement).style.boxShadow = "0 0 0 3px var(--neo-accent-light)";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--neo-border)";
                (e.currentTarget as HTMLButtonElement).style.boxShadow = "none";
              }}
            >
              <span style={{ fontSize: 32, flexShrink: 0 }}>{opt.icon}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 16, fontWeight: 700, color: "var(--neo-text)", marginBottom: 4 }}>{opt.label}</div>
                <div style={{ fontSize: 12, color: "var(--neo-text-muted)" }}>{opt.desc}</div>
              </div>
              <span style={{ fontSize: 13, fontWeight: 700, color: "var(--neo-accent)", flexShrink: 0 }}>Simulate ▶</span>
            </button>
          ))}
        </div>
      </div>

      {activeJob && (
        <OutboundSimulator jobType={activeJob} onClose={() => setActiveJob(null)} />
      )}
    </main>
  );
}
