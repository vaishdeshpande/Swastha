"""LiveKit AgentSession entrypoint — bridges LiveKit's audio stream to the
inbound LangGraph graph.

Critical config, do not change (see agents/CLAUDE.md):
- STT language MUST be "unknown" — never hardcode "hi-IN", Saaras v3
  auto-detects Hindi/Marathi/Hinglish.
- LLM MUST be sarvam-30b, not sarvam-105b — 105b's 2.06s TTFT is too slow
  for voice.
- AgentSession MUST NOT receive a vad= argument — Sarvam handles VAD
  internally via turn_detection="stt".

Integration note: livekit-agents' default pipeline calls Agent.llm_node()
with the session's configured `llm` to generate a reply. We don't want
that — the "brain" here is the LangGraph graph (which calls sarvam-30b
itself, per agent, with its own system prompts and tools), not a single
raw LLM completion. So HospitalReceptionistAgent overrides llm_node()
to run one full pass of inbound_graph per turn and return its reply as
plain text, instead of letting the default node call session.llm.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # MUST run before any module-level os.environ[...] reads below

from livekit.agents import Agent, AgentSession, JobContext, ModelSettings, WorkerOptions, cli
from livekit.agents.llm import ChatContext, Tool
from livekit.plugins import sarvam

from langsmith import trace as ls_trace

from agents.graph import inbound_graph
from agents.state import AgentState
from agents.tools.redis_tools import save_session_state

logger = logging.getLogger("livekit-agent")

SARVAM_API_KEY = os.environ["SARVAM_API_KEY"]


def _initial_state(call_id: str) -> AgentState:
    return {
        "session_id": str(uuid.uuid4()),
        "lang_code": "hi-IN",
        "tts_voice": "priya",
        "tts_model": "bulbul:v3",
        "detected_language": None,
        "detection_confidence": None,
        "patient_id": None,
        "patient_name": None,
        "is_new_patient": False,
        "intent": None,
        "department": None,
        "urgency": "normal",
        "intake_attempt_count": 0,
        "intake_collected": {},
        "messages": [],
        "current_agent": "language_router",
        "escalation_required": False,
        "escalation_reason": None,
        "call_id": call_id,
        "call_recording_path": None,
        "call_outcome": None,
        "call_start_time": datetime.now(timezone.utc).isoformat(),
        "offered_slots": None,
        "appointment_id": None,
        "job_type": None,
        "call_connected": True,
    }


class HospitalReceptionistAgent(Agent):
    """Graph-driven agent. Each turn runs ONE FULL PASS of inbound_graph —
    it doesn't stream token-by-token. The patient hears one complete,
    coherent sentence per turn, by design (see agents/CLAUDE.md)."""

    def __init__(self, call_id: str, room) -> None:
        super().__init__(instructions="Hospital receptionist voice agent (routing handled by LangGraph).")
        self.state: AgentState = _initial_state(call_id)
        self._room = room
        self._prev_agent: str | None = None

    async def _publish(self, payload: dict) -> None:
        try:
            data = json.dumps(payload).encode()
            await self._room.local_participant.publish_data(data, topic="agent-events")
        except Exception:
            logger.exception("Failed to publish agent-events data channel message")

    async def llm_node(self, chat_ctx: ChatContext, tools: list[Tool], model_settings: ModelSettings) -> str:
        logger.warning("llm_node CALLED, chat_ctx has %d messages", len(chat_ctx.messages()))
        last_user_message = None
        for message in reversed(chat_ctx.messages()):
            if message.role == "user":
                last_user_message = message.text_content
                break

        if last_user_message:
            self.state["messages"].append({"role": "user", "content": last_user_message})
            await self._publish({"type": "transcript", "role": "user", "content": last_user_message, "agent": None})

        try:
            prev_agent = self.state.get("current_agent")
            self.state = await inbound_graph.ainvoke(
                self.state,
                config={"metadata": {
                    "session_id": self.state["session_id"],
                    "call_id": self.state["call_id"],
                }},
            )
            current_agent = self.state.get("current_agent")

            # Notify frontend when the active agent changes
            if current_agent and current_agent != self._prev_agent:
                await self._publish({"type": "agent_change", "agent": current_agent})
                self._prev_agent = current_agent
        except Exception:
            logger.exception("inbound_graph failed for call %s", self.state["call_id"])
            return "Sorry, something went wrong. Let me connect you to a staff member."

        # Crash-recovery snapshot (Redis Layer 2, TTL 30 min).
        await save_session_state(self.state["call_id"], json.dumps(self.state))

        # Agent 1 can change lang_code/tts_voice mid-call — reconfigure TTS
        # to match before the reply below gets synthesized.
        self.session.tts.update_options(
            target_language_code=self.state["lang_code"],
            speaker=self.state["tts_voice"],
        )

        reply = self.state["messages"][-1]["content"] if self.state["messages"] else ""
        if reply:
            await self._publish({
                "type": "transcript",
                "role": "assistant",
                "content": reply,
                "agent": self.state.get("current_agent"),
            })

        # Send booking confirmation if an appointment was just made
        if self.state.get("appointment_id") and self.state.get("call_outcome") == "appointment_booked":
            await self._publish({
                "type": "booking_confirmed",
                "details": {
                    "doctor": self.state.get("department", ""),
                    "department": self.state.get("department"),
                    "date": None,
                    "time": None,
                },
            })

        return reply


class _TracedSTT(sarvam.STT):
    """Thin wrapper that emits a LangSmith span for every transcription."""

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id

    async def recognize(self, *args, **kwargs):
        with ls_trace(
            "stt",
            metadata={
                "session_id": self._session_id,
                "model": "saaras:v3",
                "operation": "stt",
            },
        ):
            return await super().recognize(*args, **kwargs)


class _TracedTTS(sarvam.TTS):
    """Thin wrapper that emits a LangSmith span for every synthesis call."""

    def __init__(self, session_id: str, **kwargs):
        super().__init__(**kwargs)
        self._session_id = session_id

    async def synthesize(self, text: str, **kwargs):
        with ls_trace(
            "tts",
            metadata={
                "session_id": self._session_id,
                "model": "bulbul:v3",
                "operation": "tts",
                "char_count": len(text),
            },
        ):
            return await super().synthesize(text, **kwargs)


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    call_id = ctx.room.name
    session_id = str(uuid.uuid4())  # stable for this entire call; shared with state

    stt = _TracedSTT(
        session_id=session_id,
        language="unknown",         # REQUIRED — auto-detect, never hardcode
        model="saaras:v3",
        mode="transcribe",
        flush_signal=True,          # REQUIRED — proper turn detection
    )
    tts = _TracedTTS(
        session_id=session_id,
        target_language_code="hi-IN",  # Reconfigured per turn in llm_node
        model="bulbul:v3",
        speaker="priya",
    )
    # llm-agents 1.6.4 skips reply generation entirely (never calls
    # Agent.llm_node()) if AgentSession.llm is None — see
    # agent_activity.py's `elif self.llm is None: return`. So a real LLM
    # must be supplied here even though HospitalReceptionistAgent.llm_node()
    # overrides the generation logic and never actually calls this model.
    llm = sarvam.LLM(model="sarvam-30b")

    session = AgentSession(
        stt=stt,
        tts=tts,
        llm=llm,
        turn_detection="stt",          # REQUIRED — Sarvam handles VAD internally
        min_endpointing_delay=0.07,    # REQUIRED — matches Saaras v3 processing
    )
    # DO NOT pass vad= to AgentSession — Sarvam handles this internally

    agent = HospitalReceptionistAgent(call_id, ctx.room)
    agent.state["session_id"] = session_id  # align state session_id with traced session_id
    await session.start(agent=agent, room=ctx.room)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
