"use client";

import { useEffect, useRef } from "react";
import { TranscriptMessage } from "@/lib/types";

interface TranscriptPanelProps {
  messages: TranscriptMessage[];
  langCode?: string;
}

function labelFor(role: "user" | "assistant", langCode?: string): string {
  if (langCode === "hi-IN") return role === "user" ? "मरीज़" : "एजेंट";
  if (langCode === "mr-IN") return role === "user" ? "रुग्ण" : "एजेंट";
  return role === "user" ? "Patient" : "Agent";
}

export default function TranscriptPanel({ messages, langCode }: TranscriptPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  return (
    <div className="neo-card">
      <p className="neo-label">Live Transcript</p>
      <div className="neo-inset neo-transcript">
        {messages.length === 0 && (
          <p className="neo-text-muted">Transcript will appear here once the call starts...</p>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={msg.role === "user" ? "neo-msg-patient" : "neo-msg-agent"}>
            <div className="neo-msg-label">{labelFor(msg.role, langCode)}</div>
            <div className="neo-devanagari">{msg.content}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
