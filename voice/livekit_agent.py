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

import asyncio
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path so `agents` and `api` are importable
# regardless of how this file is invoked (python voice/livekit_agent.py,
# PYTHONPATH=., IDE run configs, Railway, etc.)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")  # MUST run before any module-level os.environ[...] reads below

from livekit.agents import Agent, AgentSession, JobContext, ModelSettings, UserInputTranscribedEvent, WorkerOptions, cli
from livekit.agents.llm import ChatContext, Tool
from livekit.plugins import sarvam

from langsmith import trace as ls_trace

from agents.graph import inbound_graph
from agents.state import AgentState
from agents.tools.redis_tools import redis_get, save_session_state
from agents.tools.translate_tools import translate_text, sarvam_identify_language

logger = logging.getLogger("livekit-agent")

# Entries are either a substring (str) or a tuple of tokens that must ALL be
# present (any order, any distance). Tuples handle intervening words —
# "सीने में बहुत दर्द" contains no fixed phrase but contains both "सीने" and
# "दर्द". Gaps found by evals/dataset.yaml's emergency_utterances coverage.
EMERGENCY_KEYWORDS: dict[str, list] = {
    "hi-IN": [
        "दिल का दौरा", "सांस नहीं", "साँस नहीं", "बेहोश", "ब्रेन स्ट्रोक",
        ("सीने", "दर्द"), ("छाती", "दर्द"), ("seene", "dard"), ("chhati", "dard"),
        "saans nahi", "chest pain", "unconscious",
    ],
    "mr-IN": [
        "हृदयविकाराचा झटका", "श्वास नाही", "श्वास घेता येत", "बेशुद्ध",
        ("छाती", "दुख"), ("छातीत", "दुख"), ("chhatit", "dukh"),
    ],
    "en-IN": ["heart attack", "not breathing", "unconscious", "stroke", "chest pain", "emergency"],
}


def detect_emergency(text: str, lang_code: str) -> str | None:
    """Return the matched emergency keyword (or 'tok1 + tok2' for co-occurrence
    matches), or None. Checks the caller's language AND English — patients mix
    English emergency words into Hindi/Marathi speech.

    Module-level so evals/run_eval.py exercises the exact production logic."""
    lower = text.lower()
    for lang in {lang_code, "en-IN"}:
        for kw in EMERGENCY_KEYWORDS.get(lang, []):
            if isinstance(kw, str):
                if kw.lower() in lower:
                    return kw
            elif all(part.lower() in lower for part in kw):
                return " + ".join(kw)
    return None

MEDICAL_ADVICE_PATTERNS: list[str] = [
    "increase your dose", "double the dose", "stop taking", "stop medication",
    "avoid this medicine", "take more", "you should take more", "skip the dose",
]

SARVAM_API_KEY = os.environ["SARVAM_API_KEY"]


def _initial_state(call_id: str) -> AgentState:
    return {
        "session_id": str(uuid.uuid4()),
        "lang_code": "hi-IN",
        "tts_voice": "priya",
        "tts_model": "bulbul:v3",
        "detected_language": None,
        "detection_confidence": None,
        "lang_mismatch_count": 0,
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
        "booked_slot_details": None,
        "job_type": None,
        "call_connected": True,
        # Scenario 1–4 speculative fields
        "optimistic_patient_id": None,
        "prefetched_slots": None,
        "intent_classifier_scores": None,
        # Agent 6 (Lab Status)
        "lab_reports_dispatched": None,
        # Agent 7 (Billing)
        "bill_amount_due": None,
        "bill_sms_sent": None,
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
        self._booking_confirmed_sent: bool = False
        self._low_confidence_count: int = 0

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

        # ── Guardrail 1: Emergency keyword detection ──────────────────────────
        if last_user_message:
            matched_keyword = self._check_emergency(last_user_message)
            if matched_keyword:
                logger.warning(
                    "GUARDRAIL[emergency]: keyword=%r detected (call_id=%s)",
                    matched_keyword, self.state.get("call_id"),
                )
                emergency_reply = await translate_text(
                    "This is an emergency. Please call 108 immediately. "
                    "I am connecting you to a staff member now.",
                    source_lang="en-IN",
                    target_lang=self.state["lang_code"],
                )
                self.state["messages"].append({"role": "assistant", "content": emergency_reply})
                self.state["escalation_required"] = True
                self.state["escalation_reason"] = "Emergency keyword detected"
                await self._publish({"type": "emergency", "keyword_matched": matched_keyword})
                await self._publish({
                    "type": "transcript", "role": "assistant",
                    "content": emergency_reply, "agent": "guardrail",
                })
                return emergency_reply

        # ── Guardrail 2: STT confidence retry ────────────────────────────────
        confidence = self.state.get("detection_confidence")
        if confidence is not None and confidence < 0.3:
            self._low_confidence_count += 1
            if self._low_confidence_count <= 3:
                logger.info(
                    "GUARDRAIL[stt_retry]: confidence=%.2f count=%d (call_id=%s)",
                    confidence, self._low_confidence_count, self.state.get("call_id"),
                )
                retry_reply = await translate_text(
                    "I'm sorry, I couldn't hear you clearly. Could you please repeat that?",
                    source_lang="en-IN",
                    target_lang=self.state["lang_code"],
                )
                self.state["messages"].append({"role": "assistant", "content": retry_reply})
                await self._publish({
                    "type": "transcript", "role": "assistant",
                    "content": retry_reply, "agent": "guardrail",
                })
                return retry_reply
            # count > 3: fall through to graph — don't loop forever
        elif confidence is not None and confidence >= 0.3:
            self._low_confidence_count = 0

        # ── Main graph invocation ─────────────────────────────────────────────
        try:
            self.state = await inbound_graph.ainvoke(
                self.state,
                config={"metadata": {
                    "session_id": self.state["session_id"],
                    "call_id": self.state["call_id"],
                }},
            )
            current_agent = self.state.get("current_agent")

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

        last_msg = self.state["messages"][-1] if self.state["messages"] else {}
        if last_msg.get("role") == "assistant":
            reply = last_msg["content"]
        else:
            reply = ""
            logger.warning(
                "llm_node: graph completed without an assistant reply (last role=%s, call_id=%s)",
                last_msg.get("role"), self.state.get("call_id"),
            )

        # ── Guardrail 4: Medical boundary check (prescription agent only) ─────
        if reply and self.state.get("current_agent") == "prescription":
            reply = await self._check_medical_boundary(reply)

        # ── Guardrail 3: Output language consistency check ────────────────────
        if reply and self.state.get("lang_code") != "en-IN":
            reply = await self._enforce_output_language(reply)

        # ── Guardrail 5: TTS length cap ───────────────────────────────────────
        if reply and self.state.get("current_agent") != "post_call":
            reply = await self._cap_tts_length(reply)

        # Keep state messages in sync with the (possibly corrected) reply
        if reply and self.state["messages"] and self.state["messages"][-1].get("role") == "assistant":
            self.state["messages"][-1]["content"] = reply

        if reply:
            await self._publish({
                "type": "transcript",
                "role": "assistant",
                "content": reply,
                "agent": self.state.get("current_agent"),
            })

        # booking_confirmed — fire once when appointment_id first appears in state.
        # Use booked_slot_details (set by scheduler before clearing offered_slots)
        # so doctor name/time/date are always available for the UI card.
        if self.state.get("appointment_id") and not self._booking_confirmed_sent:
            self._booking_confirmed_sent = True
            details = self.state.get("booked_slot_details") or {}
            await self._publish({
                "type": "booking_confirmed",
                "details": {
                    "doctor": details.get("doctor_name") or self.state.get("department", ""),
                    "department": details.get("department") or self.state.get("department"),
                    "date": details.get("date"),
                    "time": details.get("time"),
                },
            })

        # lab_result_ready — fire once after Agent 6 runs and dispatches reports
        if self.state.get("current_agent") == "lab_status" and self.state.get("lab_reports_dispatched") is not None:
            await self._publish({
                "type": "lab_result_ready",
                "reports": self.state["lab_reports_dispatched"],
            })

        # bill_read — fire once after Agent 7 reads the bill amount
        if self.state.get("current_agent") == "billing" and self.state.get("bill_amount_due") is not None:
            await self._publish({
                "type": "bill_read",
                "amount": self.state["bill_amount_due"],
                "sms_sent": self.state.get("bill_sms_sent", False),
            })

        return reply

    def _check_emergency(self, text: str) -> str | None:
        """Return the first matched emergency keyword, or None."""
        return detect_emergency(text, self.state.get("lang_code", "hi-IN"))

    async def _check_medical_boundary(self, reply: str) -> str:
        """Guardrail 4: block medical advice patterns from prescription agent."""
        try:
            reply_en = await translate_text(
                reply,
                source_lang=self.state["lang_code"],
                target_lang="en-IN",
            )
        except Exception:
            logger.exception("GUARDRAIL[medical_boundary]: translation to EN failed — skipping check")
            return reply

        reply_en_lower = reply_en.lower()
        for pattern in MEDICAL_ADVICE_PATTERNS:
            if pattern in reply_en_lower:
                logger.warning(
                    "GUARDRAIL[medical_boundary]: blocked pattern=%r (call_id=%s). Original: %s",
                    pattern, self.state.get("call_id"), reply,
                )
                self.state["escalation_required"] = True
                self.state["escalation_reason"] = "Medical advice boundary crossed"
                safe_reply = await translate_text(
                    "For any changes to your medication, please consult your doctor directly. "
                    "I can only share what is on your prescription.",
                    source_lang="en-IN",
                    target_lang=self.state["lang_code"],
                )
                return safe_reply
        return reply

    async def _enforce_output_language(self, reply: str) -> str:
        """Guardrail 3: translate reply to state lang_code if LLM responded in wrong language."""
        try:
            detected = await sarvam_identify_language(reply)
        except Exception:
            logger.exception("GUARDRAIL[lang_consistency]: identify_language failed — skipping check")
            return reply

        expected = self.state.get("lang_code", "hi-IN")
        if detected != expected:
            logger.warning(
                "GUARDRAIL[lang_consistency]: reply lang=%s, expected=%s — correcting (call_id=%s)",
                detected, expected, self.state.get("call_id"),
            )
            try:
                return await translate_text(reply, source_lang=detected, target_lang=expected)
            except Exception:
                logger.exception("GUARDRAIL[lang_consistency]: correction translation failed — using original")
        return reply

    async def _cap_tts_length(self, reply: str) -> str:
        """Guardrail 5: summarise replies longer than 300 chars before TTS."""
        if len(reply) <= 300:
            return reply

        logger.info(
            "GUARDRAIL[tts_cap]: reply length=%d > 300 — summarising (call_id=%s)",
            len(reply), self.state.get("call_id"),
        )
        lang_code = self.state.get("lang_code", "hi-IN")
        try:
            import os as _os
            from sarvamai import SarvamAI as _SarvamAI
            _client = _SarvamAI(api_subscription_key=_os.environ["SARVAM_API_KEY"])
            with ls_trace(
                "sarvam-30b:tts_cap_summarise",
                run_type="llm",
                inputs={"reply_chars": len(reply), "lang_code": lang_code},
            ):
                resp = await asyncio.to_thread(
                    lambda: _client.chat.completions(
                        messages=[{
                            "role": "user",
                            "content": f"Summarise this in under 40 words in {lang_code}: {reply}",
                        }],
                        model="sarvam-30b",
                    )
                )
            summarised = resp.choices[0].message.content.strip()
            logger.debug("GUARDRAIL[tts_cap]: original=%d chars, summarised=%d chars", len(reply), len(summarised))
            return summarised
        except Exception:
            logger.exception("GUARDRAIL[tts_cap]: summarisation failed — using original reply")
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

    # Reconnect recovery — restore state from Redis if this room was active recently.
    # LiveKit reuses the same room name on reconnect, so call_id (= room name) is
    # stable across the disconnect/reconnect cycle. If a session snapshot exists
    # (TTL 30 min), restore it so the patient doesn't have to repeat themselves.
    try:
        saved_json = await redis_get(f"session:{call_id}")
        if saved_json:
            import json as _json
            restored = _json.loads(saved_json)
            # Preserve the new session_id — the old one is stale after reconnect
            restored["session_id"] = session_id
            # Reset lang_mismatch_count — STT context is fresh after reconnect
            restored["lang_mismatch_count"] = 0
            agent.state = restored
            logger.info(
                "entrypoint: restored session from Redis for call_id=%s "
                "(patient_id=%s, intent=%s, messages=%d)",
                call_id, restored.get("patient_id"), restored.get("intent"), len(restored.get("messages", [])),
            )
        else:
            logger.info("entrypoint: no saved session for call_id=%s — starting fresh", call_id)
    except Exception:
        logger.exception("entrypoint: failed to restore session from Redis for call_id=%s — starting fresh", call_id)

    # Wait for the patient participant and read their preferred_lang from token metadata.
    # The token API embeds {"preferred_lang": "mr-IN"/"hi-IN"/"auto"} at issue time.
    # wait_for_participant() returns immediately if the participant is already in the room,
    # or waits up to 10s — handles the race between agent connect and patient connect.
    try:
        participant = await asyncio.wait_for(ctx.wait_for_participant(), timeout=10.0)
        meta = json.loads(participant.metadata or "{}")
        pref = meta.get("preferred_lang", "auto")
        logger.info("entrypoint: participant metadata preferred_lang=%r", pref)
        if pref and pref != "auto":
            from agents.tools.language_config import load_language_config
            lang_cfg = load_language_config(pref)
            agent.state["detected_language"] = pref
            agent.state["detection_confidence"] = 1.0
            agent.state["lang_code"] = pref
            agent.state["tts_voice"] = lang_cfg["tts_voice"]
            agent.state["tts_model"] = lang_cfg["tts_model"]
            logger.info("entrypoint: forced lang_code=%s tts_voice=%s", pref, lang_cfg["tts_voice"])
    except Exception:
        logger.exception("entrypoint: failed to read participant metadata, falling back to auto-detect")

    # Capture STT-detected language from each transcription event and stamp it on state
    # BEFORE llm_node runs. This is the most reliable source — Saaras v3 with
    # language="unknown" returns detected_language per utterance. Without this,
    # language_router falls back to the async identify_language API (correct but adds latency).
    _KNOWN_LANGS = {"hi-IN", "mr-IN", "en-IN", "kn-IN", "ta-IN", "te-IN", "bn-IN", "gu-IN"}

    def _on_user_input_transcribed(ev: UserInputTranscribedEvent) -> None:
        if ev.is_final and ev.language:
            lang_str = str(ev.language)
            # Only accept concrete language codes — Sarvam returns "unknown" in
            # streaming mode when language="unknown" is passed for auto-detect.
            # "unknown" here means "not yet identified", not a real language.
            if lang_str in _KNOWN_LANGS:
                logger.info("STT detected concrete language: %s", lang_str)
                agent.state["detected_language"] = lang_str
                agent.state["detection_confidence"] = 1.0
            else:
                logger.info("STT returned non-concrete language=%s, leaving fallback active", lang_str)

    session.on("user_input_transcribed", _on_user_input_transcribed)

    # Sync TTS to the pre-detected language before session starts
    session.tts.update_options(
        target_language_code=agent.state["lang_code"],
        speaker=agent.state["tts_voice"],
    )

    await session.start(agent=agent, room=ctx.room)

    # Speak the greeting in the pre-detected/default language immediately on connect.
    # load_language_config pulls the greeting string from config/languages.yaml so
    # adding a new language there automatically gives it a custom greeting too.
    from agents.tools.language_config import load_language_config
    greeting = load_language_config(agent.state["lang_code"]).get(
        "greeting", "नमस्ते! मैं आपकी कैसे मदद कर सकती हूँ?"
    )
    await session.say(greeting, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
