export type LangOption = "hi-IN" | "mr-IN" | "auto";

export const LANGUAGE_OPTIONS: { code: LangOption; label: string }[] = [
  { code: "hi-IN", label: "Hindi" },
  { code: "mr-IN", label: "Marathi" },
  { code: "auto", label: "Auto" },
];

export const AGENT_ORDER = [
  { key: "language_router", label: "Language Router", sublabel: "Detects language" },
  { key: "voice_intake", label: "Voice Intake", sublabel: "Understands intent" },
  { key: "scheduler", label: "Appointment Scheduler", sublabel: "Books slots" },
  { key: "prescription", label: "Prescription Agent", sublabel: "Reads medication" },
  { key: "lab_status", label: "Lab Status", sublabel: "Report lookup" },
  { key: "billing", label: "Billing Agent", sublabel: "Bill + payment link" },
  { key: "followup", label: "Follow-up Agent", sublabel: "Post-discharge" },
] as const;

export type AgentKey = (typeof AGENT_ORDER)[number]["key"];

export type CallStatus =
  | "idle"
  | "connecting"
  | "greeting"
  | "listening"
  | "processing"
  | "speaking"
  | "ending"
  | "ended"
  | "call_dropped";

export interface TranscriptMessage {
  role: "user" | "assistant";
  content: string;
  agent?: string;
  timestamp: number;
}

export interface BookingDetails {
  doctor: string;
  department?: string;
  date?: string;
  time: string;
}

export interface LabReport {
  test_name: string;
  summary: string;
  status?: "ready" | "pending";
}

export interface BillDetails {
  amount: number;
  sms_sent: boolean;
}

export interface CallSummary {
  durationSec: number;
  lang: LangOption;
  intent?: string;
  agentsUsed: string[];
  dropped: boolean;
  dropReason?: string;
}

export type AgentEvent =
  | { type: "transcript"; role: "user" | "assistant"; content: string; agent?: string; timestamp?: string }
  | { type: "agent_change"; agent: string }
  | { type: "status_change"; status: CallStatus }
  | { type: "booking_confirmed"; details: BookingDetails }
  | { type: "lab_result_ready"; reports: LabReport[] }
  | { type: "bill_read"; amount: number; sms_sent: boolean }
  | { type: "call_dropped"; reason: string }
  | { type: "error"; message: string };
