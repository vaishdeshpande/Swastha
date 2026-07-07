import { createLogger } from "@/lib/logger";

const log = createLogger("lib/livekit");

export function getLiveKitUrl(): string {
  const url = process.env.NEXT_PUBLIC_LIVEKIT_URL;
  if (!url) {
    log.error("NEXT_PUBLIC_LIVEKIT_URL is not set");
    throw new Error("NEXT_PUBLIC_LIVEKIT_URL is not set");
  }
  log.debug("LiveKit URL resolved", { url });
  return url;
}

export function getBackendUrl(): string {
  const url = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
  log.debug("Backend URL resolved", { url });
  return url;
}

export function generateRoomName(): string {
  const name = `hospital-${Date.now()}`;
  log.debug("Room name generated", { name });
  return name;
}

export function generateParticipantName(): string {
  const name = `patient-${Math.random().toString(36).slice(2, 8)}`;
  log.debug("Participant name generated", { name });
  return name;
}
