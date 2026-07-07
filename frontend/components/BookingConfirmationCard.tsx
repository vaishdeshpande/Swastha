"use client";

import { BookingDetails, LangOption } from "@/lib/types";
import { localizedLabel } from "@/lib/storedResponses";

interface Props {
  details: BookingDetails;
  langCode?: LangOption;
}

export default function BookingConfirmationCard({ details, langCode }: Props) {
  const title = localizedLabel(
    langCode ?? "auto",
    "अपॉइंटमेंट कन्फर्म हो गया",
    "अपॉइंटमेंट कन्फर्म झाले",
    "Appointment Confirmed",
  );

  return (
    <div className="neo-booking-card">
      <div className="neo-booking-header">
        <span className="neo-check-circle">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        </span>
        <p className="neo-text" style={{ fontWeight: 600 }}>
          {title}
        </p>
      </div>
      <div className="neo-booking-grid">
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Doctor</div>
          <div className="neo-booking-cell-val">{details.doctor}</div>
        </div>
        {details.department && (
          <div className="neo-booking-cell">
            <div className="neo-booking-cell-label">Department</div>
            <div className="neo-booking-cell-val">{details.department}</div>
          </div>
        )}
        {details.date && (
          <div className="neo-booking-cell">
            <div className="neo-booking-cell-label">Date</div>
            <div className="neo-booking-cell-val">{details.date}</div>
          </div>
        )}
        <div className="neo-booking-cell">
          <div className="neo-booking-cell-label">Time</div>
          <div className="neo-booking-cell-val">{details.time}</div>
        </div>
      </div>
    </div>
  );
}
