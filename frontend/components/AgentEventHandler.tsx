"use client";

import { useDataChannel } from "@livekit/components-react";
import { createLogger } from "@/lib/logger";
import { AgentEvent, BookingDetails, CallStatus, TranscriptMessage } from "@/lib/types";

const log = createLogger("component/AgentEventHandler");

interface AgentEventHandlerProps {
  onTranscript: (msg: TranscriptMessage) => void;
  onAgentChange: (agent: string) => void;
  onStatusChange: (status: CallStatus) => void;
  onBookingConfirmed: (details: BookingDetails) => void;
}

export default function AgentEventHandler({
  onTranscript,
  onAgentChange,
  onStatusChange,
  onBookingConfirmed,
}: AgentEventHandlerProps) {
  useDataChannel("agent-events", (msg) => {
    let event: AgentEvent;
    try {
      const raw = new TextDecoder().decode(msg.payload);
      event = JSON.parse(raw) as AgentEvent;
    } catch (err) {
      log.warn("Failed to parse data-channel payload", { error: String(err) });
      return;
    }

    log.debug("Agent event received", { type: event.type });

    switch (event.type) {
      case "transcript":
        onTranscript({
          role: event.role,
          content: event.content,
          agent: event.agent,
          timestamp: Date.now(),
        });
        break;
      case "agent_change":
        onAgentChange(event.agent);
        break;
      case "status_change":
        onStatusChange(event.status);
        break;
      case "booking_confirmed":
        onBookingConfirmed(event.details);
        break;
      case "call_dropped":
        log.warn("Agent reported call dropped", { reason: event.reason });
        break;
      case "error":
        log.error("Agent reported error", { message: event.message });
        break;
    }
  });

  return null;
}
