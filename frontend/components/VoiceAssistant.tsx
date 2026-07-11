"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { LiveKitRoom, RoomAudioRenderer, useConnectionState, useRoomContext } from "@livekit/components-react";
import type { DisconnectReason } from "livekit-client";
import { ConnectionState } from "livekit-client";
import "@livekit/components-styles";
import CallControls from "@/components/CallControls";
import AgentEventHandler from "@/components/AgentEventHandler";
import TranscriptPanel from "@/components/TranscriptPanel";
import BookingConfirmationCard from "@/components/BookingConfirmationCard";
import LabResultCard from "@/components/LabResultCard";
import BillCard from "@/components/BillCard";
import CallSummaryCard from "@/components/CallSummaryCard";
import { createLogger } from "@/lib/logger";
import { getBackendUrl, getLiveKitUrl, generateParticipantName, generateRoomName } from "@/lib/livekit";
import {
  BillDetails,
  BookingDetails,
  CallStatus,
  CallSummary,
  LangOption,
  LabReport,
  TranscriptMessage,
} from "@/lib/types";

const log = createLogger("component/VoiceAssistant");

const LANG_LABEL: Record<LangOption, string> = {
  "hi-IN": "Hindi",
  "mr-IN": "Marathi",
  auto: "Auto",
};

// Greeting text must match config/languages.yaml so the transcript is consistent
// with what the cached audio says.
const GREETING_TEXT: Record<string, string> = {
  "hi-IN": "नमस्कार! अपोलो हॉस्पिटल्स में आपका स्वागत है। मैं स्वस्था हूँ, आपकी एआई स्वास्थ्य सहायक। अपॉइंटमेंट बुक करने, सही डॉक्टर या विभाग की जानकारी देने, अस्पताल की सेवाओं से जुड़े सवालों के जवाब देने और आपको उचित टीम से जोड़ने में मैं आपकी मदद कर सकती हूँ। कृपया बताइए, आज मैं आपकी कैसे सहायता कर सकती हूँ?",
  "mr-IN": "नमस्कार! अपोलो हॉस्पिटल्समध्ये आपले स्वागत आहे. मी स्वस्था आहे, तुमची एआय आरोग्य सहाय्यक. अपॉइंटमेंट बुक करणे, योग्य डॉक्टर किंवा विभागाची माहिती देणे, रुग्णालयाच्या सेवांबाबत माहिती देणे आणि योग्य विभागाशी जोडणे यासाठी मी तुमची मदत करू शकते. कृपया सांगा, आज मी तुमची कशी मदत करू?",
};

function formatElapsed(sec: number): string {
  const mm = String(Math.floor(sec / 60)).padStart(2, "0");
  const ss = String(sec % 60).padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function VoiceAssistant() {
  const [callStatus, setCallStatus] = useState<CallStatus>("idle");
  const [token, setToken] = useState<string | null>(null);
  const [preferredLang, setPreferredLang] = useState<LangOption>("auto");
  const [transcript, setTranscript] = useState<TranscriptMessage[]>([]);
  const [activeAgent, setActiveAgent] = useState<string | null>(null);
  const [completedAgents, setCompletedAgents] = useState<string[]>([]);
  const [bookingDetails, setBookingDetails] = useState<BookingDetails | null>(null);
  const [labResults, setLabResults] = useState<LabReport[] | null>(null);
  const [billDetails, setBillDetails] = useState<BillDetails | null>(null);
  const [summary, setSummary] = useState<CallSummary | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  const callId = useRef<string>("");
  const displayId = useRef<string>("HSP-—");
  const callStartedAt = useRef<number>(0);
  const greetingAudioRef = useRef<Record<string, HTMLAudioElement>>({});

  // Initialise random IDs on client only to avoid SSR/hydration mismatch
  useEffect(() => {
    if (!callId.current) {
      callId.current = generateRoomName();
      displayId.current = `HSP-${Math.floor(1000 + Math.random() * 9000)}`;
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Preload greeting WAV files so they're in browser cache before the user clicks.
  useEffect(() => {
    const audios: Record<string, HTMLAudioElement> = {
      "hi-IN": new Audio("/greetings/hi-IN.wav"),
      "mr-IN": new Audio("/greetings/mr-IN.wav"),
    };
    Object.values(audios).forEach((a) => a.load());
    greetingAudioRef.current = audios;
  }, []);

  const isInCall = callStatus !== "idle" && callStatus !== "ended" && callStatus !== "call_dropped";
  const showSummary = callStatus === "ended" || callStatus === "call_dropped";

  useEffect(() => {
    if (!isInCall) return;
    const id = setInterval(() => {
      setElapsedSec(Math.round((Date.now() - callStartedAt.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [isInCall]);

  // The greeting plays as a local HTMLAudioElement outside the LiveKit room,
  // so ending/dropping the call does not stop it automatically.
  const stopGreetingAudio = useCallback(() => {
    Object.values(greetingAudioRef.current).forEach((audio) => {
      if (!audio.paused) {
        audio.pause();
        audio.currentTime = 0;
      }
    });
  }, []);

  const resetCallState = useCallback(() => {
    setTranscript([]);
    setActiveAgent(null);
    setCompletedAgents([]);
    setBookingDetails(null);
    setLabResults(null);
    setBillDetails(null);
    setElapsedSec(0);
  }, []);

  const startCall = useCallback(async () => {
    log.info("Starting call", { preferredLang, room: callId.current });

    // Play cached greeting instantly — no network round-trip needed.
    // "auto" falls back to Hindi (the backend default).
    const greetingLang = preferredLang === "auto" ? "hi-IN" : preferredLang;
    const greetingText = GREETING_TEXT[greetingLang];
    const audio = greetingAudioRef.current[greetingLang];
    if (audio) {
      audio.currentTime = 0;
      audio.play().catch((e) => log.warn("Cached greeting playback blocked", { error: String(e) }));
    }
    if (greetingText) {
      setTranscript([{ role: "assistant", content: greetingText, timestamp: Date.now() }]);
    }

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
      stopGreetingAudio();
      setCallStatus("idle");
    }
  }, [preferredLang, stopGreetingAudio]);

  const endCall = useCallback(() => {
    log.info("User ended call");
    stopGreetingAudio();
    setCallStatus("ending");
  }, [stopGreetingAudio]);

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
      stopGreetingAudio();
      const durationSec = (Date.now() - callStartedAt.current) / 1000;
      setCallStatus((current) => {
        const userInitiated = current === "ending";
        if (userInitiated) {
          setSummary({ durationSec, lang: preferredLang, agentsUsed: completedAgents, dropped: false });
          setToken(null);
          return "ended";
        }
        log.warn("Unexpected LiveKit disconnect", { reason });
        logDroppedCall(String(reason));
        setSummary({ durationSec, lang: preferredLang, agentsUsed: completedAgents, dropped: true, dropReason: String(reason ?? "unknown") });
        setToken(null);
        return "call_dropped";
      });
    },
    [completedAgents, logDroppedCall, preferredLang, stopGreetingAudio],
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
    displayId.current = `HSP-${Math.floor(1000 + Math.random() * 9000)}`;
    setSummary(null);
    resetCallState();
    setCallStatus("idle"); // triggers re-render which picks up new displayId
  }, [resetCallState]);

  const reconnect = useCallback(() => {
    setSummary(null);
    resetCallState();
    startCall();
  }, [startCall, resetCallState]);

  const simulateDrop = useCallback(() => {
    stopGreetingAudio();
    setSummary({
      durationSec: elapsedSec,
      lang: preferredLang,
      agentsUsed: completedAgents,
      dropped: true,
      dropReason: "simulated",
    });
    setToken(null);
    setCallStatus("call_dropped");
  }, [elapsedSec, preferredLang, completedAgents, stopGreetingAudio]);

  return (
    <div className="neo-screen">
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 28 }}>
        <div style={{
          width: 44, height: 44, borderRadius: "50%",
          background: "var(--neo-bg)",
          boxShadow: "4px 4px 10px var(--neo-shadow-dark), -4px -4px 10px var(--neo-shadow-light)",
          display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0,
        }}>
          <div style={{ width: 14, height: 14, borderRadius: "50%", background: "var(--neo-accent)" }} />
        </div>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, color: "var(--neo-text)", letterSpacing: "-0.01em" }}>
            Swastha AI
          </div>
          <div style={{ fontSize: 12, color: "var(--neo-text-muted)", marginTop: 2 }}>
            Hospital Voice Receptionist
          </div>
        </div>
      </div>

      {/* Two-column layout */}
      <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 24, alignItems: "start" }}>

        {/* Left column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          {/* Main call control card */}
          <div className="neo-card" style={{
            minHeight: 340,
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center",
            gap: 16, textAlign: "center", boxSizing: "border-box",
            padding: "28px 24px",
          }}>
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
                onSimulateDrop={simulateDrop}
              />
            )}
          </div>

          {bookingDetails && <BookingConfirmationCard details={bookingDetails} langCode={preferredLang} />}
          {labResults && <LabResultCard reports={labResults} langCode={preferredLang} />}
          {billDetails && <BillCard details={billDetails} langCode={preferredLang} />}
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20, minWidth: 0 }}>

          {/* Session bar */}
          <div className="neo-card" style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            gap: 16, padding: "18px 22px",
          }}>
            <div>
              <div className="neo-label" style={{ marginBottom: 5 }}>Session</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: "var(--neo-text)", fontFamily: "monospace" }}>
                {displayId.current}
              </div>
            </div>
            <div className="neo-session-divider" />
            <div style={{ textAlign: "center" }}>
              <div className="neo-label" style={{ marginBottom: 5 }}>Duration</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: "var(--neo-text)", fontVariantNumeric: "tabular-nums" }}>
                {formatElapsed(elapsedSec)}
              </div>
            </div>
            <div className="neo-session-divider" />
            <div style={{ textAlign: "right" }}>
              <div className="neo-label" style={{ marginBottom: 5 }}>Language</div>
              <div style={{ fontSize: 14, fontWeight: 800, color: "var(--neo-accent)" }}>
                {LANG_LABEL[preferredLang]}
              </div>
            </div>
          </div>

          {/* Transcript */}
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
          <DisconnectOnEnd callStatus={callStatus} />
          <AgentEventHandler
            onTranscript={handleTranscript}
            onAgentChange={handleAgentChange}
            onStatusChange={handleStatusChange}
            onBookingConfirmed={setBookingDetails}
            onLabResult={setLabResults}
            onBillRead={setBillDetails}
          />
        </LiveKitRoom>
      )}
    </div>
  );
}

function DisconnectOnEnd({ callStatus }: { callStatus: CallStatus }) {
  const room = useRoomContext();
  useEffect(() => {
    if (callStatus === "ending") {
      room.disconnect();
    }
  }, [callStatus, room]);
  return null;
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
