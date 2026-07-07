"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveKitRoom, RoomAudioRenderer, useConnectionState, useRoomContext } from "@livekit/components-react";
import type { DisconnectReason } from "livekit-client";
import { ConnectionState } from "livekit-client";
import "@livekit/components-styles";
import CallControls from "@/components/CallControls";
import AgentEventHandler from "@/components/AgentEventHandler";
import AgentActivityFeed from "@/components/AgentActivityFeed";
import TranscriptPanel from "@/components/TranscriptPanel";
import BookingConfirmationCard from "@/components/BookingConfirmationCard";
import CallSummaryCard from "@/components/CallSummaryCard";
import { createLogger } from "@/lib/logger";
import { getBackendUrl, getLiveKitUrl, generateParticipantName, generateRoomName } from "@/lib/livekit";
import {
  BookingDetails,
  CallStatus,
  CallSummary,
  LangOption,
  TranscriptMessage,
} from "@/lib/types";

const log = createLogger("component/VoiceAssistant");

export default function VoiceAssistant() {
  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [token, setToken] = useState<string | null>(null);
  const [preferredLang, setPreferredLang] = useState<LangOption>("auto");
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [completedAgents, setCompletedAgents] = useState<string[]>([]);
  const [bookingDetails, setBookingDetails] = useState<BookingDetails | null>(null);
  const [summary, setSummary] = useState<CallSummary | null>(null);

  const callId = useRef<string>(generateRoomName());
  const callStartedAt = useRef<number>(0);

  const resetCallState = useCallback(() => {
    setTranscript([]);
    setActiveAgent(null);
    setCompletedAgents([]);
    setBookingDetails(null);
  }, []);

  const startCall = useCallback(async () => {
    log.info("Starting call", { preferredLang, room: callId.current });
    setCallStatus("connecting");
    try {
      const res = await fetch("/api/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          room: callId.current,
          participant: generateParticipantName(),
          preferred_lang: preferredLang,
        }),
      });
      if (!res.ok) throw new Error("Failed to fetch access token");
      const data = await res.json();
      callStartedAt.current = Date.now();
      setToken(data.token);
    } catch (err) {
      log.error("Failed to start call", { error: err instanceof Error ? err.message : String(err) });
      setCallStatus("idle");
    }
  }, [preferredLang]);

  const endCall = useCallback(() => {
    log.info("User ended call");
    setCallStatus("ending");
  }, []);

  const logDroppedCall = useCallback(async (reason?: string) => {
    try {
      await fetch(`${getBackendUrl()}/api/followup/log`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          call_id: callId.current,
          outcome: { status: "dropped", reason: reason ?? "unknown" },
        }),
      });
    } catch (err) {
      log.warn("Failed to log dropped call", { error: String(err) });
    }
  }, []);

  const handleDisconnect = useCallback(
    (reason?: DisconnectReason) => {
      const durationSec = (Date.now() - callStartedAt.current) / 1000;
      setCallStatus((current) => {
        const userInitiated = current === "ending";
        if (userInitiated) {
          setSummary({
            durationSec,
            lang: preferredLang,
            agentsUsed: completedAgents,
            dropped: false,
          });
          setToken(null);
          return "ended";
        }
        log.warn("Unexpected LiveKit disconnect", { reason });
        logDroppedCall(String(reason));
        setSummary({
          durationSec,
          lang: preferredLang,
          agentsUsed: completedAgents,
          dropped: true,
          dropReason: String(reason ?? "unknown"),
        });
        setToken(null);
        return "call_dropped";
      });
    },
    [completedAgents, logDroppedCall, preferredLang],
  );

  const handleTranscript = useCallback((msg: TranscriptMessage) => {
    setTranscript((prev) => [...prev, msg]);
  }, []);

  const handleAgentChange = useCallback((agent: string) => {
    setActiveAgent((prevActive) => {
      if (prevActive && prevActive !== agent) {
        setCompletedAgents((prev) => (prev.includes(prevActive) ? prev : [...prev, prevActive]));
      }
      return agent;
    });
  }, []);

  const handleStatusChange = useCallback((status: CallStatus) => {
    setCallStatus((current) => (current === "ending" ? current : status));
  }, []);

  const startNewCall = useCallback(() => {
    callId.current = generateRoomName();
    setSummary(null);
    resetCallState();
    setCallStatus("idle");
  }, [resetCallState]);

  const reconnect = useCallback(() => {
    setSummary(null);
    setCallStatus("connecting");
    startCall();
  }, [startCall]);

  const showSummary = callStatus === "ended" || callStatus === "call_dropped";

  return (
    <div className="neo-screen">
      <header className="mb-6 text-center">
        <h1 className="text-xl font-bold" style={{ color: "var(--neo-text)" }}>
          🏥 Swastha AI — Hospital Voice Receptionist
        </h1>
        <p className="neo-text-muted">Speak in Hindi, Marathi, or Hinglish — powered by Sarvam AI</p>
      </header>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <div>
          <div className="neo-card flex flex-col items-center">
            {showSummary && summary ? (
              <CallSummaryCard
                summary={summary}
                onStartNewCall={startNewCall}
                onReconnect={summary.dropped ? reconnect : undefined}
              />
            ) : (
              <CallControls
                callStatus={callStatus}
                preferredLang={preferredLang}
                onLangChange={setPreferredLang}
                onStartCall={startCall}
                onEndCall={endCall}
              />
            )}
          </div>
          {bookingDetails && <BookingConfirmationCard details={bookingDetails} langCode={preferredLang} />}
        </div>

        <div className="flex flex-col gap-6">
          <AgentActivityFeed activeAgent={activeAgent} completedAgents={completedAgents} />
          <TranscriptPanel messages={transcript} langCode={preferredLang} />
        </div>
      </div>

      {token && (
        <LiveKitRoom
          token={token}
          serverUrl={getLiveKitUrl()}
          connect
          audio
          onConnected={() => setCallStatus("greeting")}
          onDisconnected={handleDisconnect}
        >
          <RoomAudioRenderer />
          <ParticipantMetadataSync preferredLang={preferredLang} />
          <AgentEventHandler
            onTranscript={handleTranscript}
            onAgentChange={handleAgentChange}
            onStatusChange={handleStatusChange}
            onBookingConfirmed={setBookingDetails}
          />
        </LiveKitRoom>
      )}
    </div>
  );
}

function ParticipantMetadataSync({ preferredLang }: { preferredLang: LangOption }) {
  const room = useRoomContext();
  const connectionState = useConnectionState();

  useEffect(() => {
    if (preferredLang !== "auto" && connectionState === ConnectionState.Connected) {
      room.localParticipant.setMetadata(JSON.stringify({ preferred_lang: preferredLang }));
    }
  }, [room, preferredLang, connectionState]);

  return null;
}
