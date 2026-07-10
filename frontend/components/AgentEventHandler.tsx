"use client";

import { useDataChannel } from "@livekit/components-react";
import { createLogger } from "@/lib/logger";
import { AgentEvent, BillDetails, BookingDetails, CallStatus, LabReport, TranscriptMessage } from "@/lib/types";

const log = createLogger("component/AgentEventHandler");

interface AgentEventHandlerProps {
  onTranscript: (msg: TranscriptMessage) => void;
  onAgentChange: (agent: string) => void;
  onStatusChange: (status: CallStatus) => void;
  onBookingConfirmed: (details: BookingDetails) => void;
  onLabResult?: (reports: LabReport[]) => void;
  onBillRead?: (details: BillDetails) => void;
}

export default function AgentEventHandler({
  onTranscript,
  onAgentChange,
  onStatusChange,
  onBookingConfirmed,
  onLabResult,
  onBillRead,
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
      case "lab_result_ready":
        onLabResult?.(event.reports);
        break;
      case "bill_read":
        onBillRead?.({ amount: event.amount, sms_sent: event.sms_sent });
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
