"use client";

import { BillDetails, LangOption } from "@/lib/types";

interface Props {
  details: BillDetails;
  langCode?: LangOption;
}

function headerLabel(lang?: LangOption): string {
  if (lang === "hi-IN") return "बकाया Bill";
  if (lang === "mr-IN") return "बाकी Bill";
  return "Outstanding Bill";
}

function smsLabel(lang?: LangOption): string {
  if (lang === "hi-IN") return "Payment link भेज दिया गया";
  if (lang === "mr-IN") return "Payment link पाठवला गेला";
  return "Payment link sent to your mobile";
}

export default function BillCard({ details, langCode }: Props) {
  return (
    <div className="neo-booking-card">
      <div className="neo-booking-header">
        <span className="neo-check-circle" style={{ background: "var(--neo-amber)" }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5">
            <rect x="2" y="5" width="20" height="14" rx="2" />
            <line x1="2" y1="10" x2="22" y2="10" />
          </svg>
        </span>
        <p className="neo-text" style={{ fontWeight: 600 }}>
          {headerLabel(langCode)}
        </p>
      </div>
      <div className="neo-booking-grid">
        <div className="neo-booking-cell" style={{ gridColumn: "1 / -1" }}>
          <div className="neo-booking-cell-label">Amount</div>
          <div className="neo-booking-cell-val" style={{ fontSize: 18, color: "var(--neo-accent)" }}>
            ₹{details.amount.toLocaleString("en-IN")}
          </div>
        </div>
        {details.sms_sent && (
          <div className="neo-booking-cell" style={{ gridColumn: "1 / -1" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: "var(--neo-green)",
                  flexShrink: 0,
                }}
              />
              <span className="neo-text-sm neo-devanagari">{smsLabel(langCode)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
