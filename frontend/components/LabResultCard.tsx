"use client";

import { LabReport, LangOption } from "@/lib/types";

interface Props {
  reports: LabReport[];
  langCode?: LangOption;
}

function headerLabel(lang?: LangOption): string {
  if (lang === "hi-IN") return "आपकी Lab Reports";
  if (lang === "mr-IN") return "तुमचे Lab Reports";
  return "Lab Reports";
}

export default function LabResultCard({ reports, langCode }: Props) {
  return (
    <div className="neo-booking-card">
      <div className="neo-booking-header">
        <span className="neo-check-circle" style={{ background: "var(--neo-accent)" }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5">
            <path d="M9 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9l-6-6z" />
            <polyline points="9 3 9 9 15 9" />
          </svg>
        </span>
        <p className="neo-text" style={{ fontWeight: 600 }}>
          {headerLabel(langCode)}
        </p>
      </div>
      <div className="flex flex-col gap-2">
        {reports.map((r, i) => (
          <div key={i} className="neo-booking-cell flex items-start justify-between gap-2">
            <div>
              <div className="neo-booking-cell-label">{r.test_name}</div>
              <div className="neo-booking-cell-val neo-devanagari">{r.summary}</div>
            </div>
            {r.status === "pending" && (
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: "var(--neo-amber)",
                  flexShrink: 0,
                  marginTop: 4,
                }}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
