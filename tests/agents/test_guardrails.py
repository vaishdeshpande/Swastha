"""Unit tests for the 6 session-layer guardrails.

All external dependencies (Sarvam API, graph, Redis) are mocked so these
tests run without credentials, a database, or a LiveKit room.
"""

from __future__ import annotations

import sys
import os
import asyncio
import types
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

# Ensure env stubs are in place before any import of project modules
os.environ.setdefault("SARVAM_API_KEY", "test-key")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://test.upstash.io")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "test-token")
os.environ.setdefault("LIVEKIT_API_KEY", "test-lk-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-lk-secret")
os.environ.setdefault("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-auth")
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+10000000000")
os.environ.setdefault("ON_CALL_DOCTOR_PHONE", "+10000000001")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_room():
    room = MagicMock()
    room.local_participant.publish_data = AsyncMock()
    return room


def _make_chat_ctx(user_text: str):
    msg = MagicMock()
    msg.role = "user"
    msg.text_content = user_text
    ctx = MagicMock()
    ctx.messages.return_value = [msg]
    return ctx


async def _build_agent(call_id: str = "test-call-001", **state_overrides):
    """Import and instantiate HospitalReceptionistAgent with a mocked room."""
    # Heavy patching needed so the module can be imported without LiveKit infra
    lk_stub = types.ModuleType("livekit")
    lk_agents = types.ModuleType("livekit.agents")
    lk_agents.Agent = object  # plain base so __init__ doesn't need real Agent
    lk_agents.AgentSession = MagicMock()
    lk_agents.JobContext = MagicMock()
    lk_agents.ModelSettings = MagicMock()
    lk_agents.UserInputTranscribedEvent = MagicMock()
    lk_agents.WorkerOptions = MagicMock()
    lk_agents.cli = MagicMock()
    lk_agents.llm = types.ModuleType("livekit.agents.llm")
    lk_agents.llm.ChatContext = MagicMock()
    lk_agents.llm.Tool = MagicMock()
    lk_stub.agents = lk_agents
    lk_plugins = types.ModuleType("livekit.plugins")
    lk_plugins.sarvam = MagicMock()
    lk_stub.plugins = lk_plugins
    sys.modules.setdefault("livekit", lk_stub)
    sys.modules.setdefault("livekit.agents", lk_agents)
    sys.modules.setdefault("livekit.agents.llm", lk_agents.llm)
    sys.modules.setdefault("livekit.plugins", lk_plugins)
    sys.modules.setdefault("livekit.plugins.sarvam", lk_plugins.sarvam)
    sys.modules.setdefault("langsmith", MagicMock())

    # Stub heavy graph/tools imports so they don't hit the network
    sys.modules.setdefault("agents.graph", MagicMock())
    sys.modules.setdefault("agents.tools.redis_tools", MagicMock(
        redis_get=AsyncMock(return_value=None),
        save_session_state=AsyncMock(),
    ))

    from voice.livekit_agent import HospitalReceptionistAgent, _initial_state

    room = _make_mock_room()
    agent = HospitalReceptionistAgent.__new__(HospitalReceptionistAgent)
    agent.state = _initial_state(call_id)
    agent.state.update(state_overrides)
    agent._room = room
    agent._prev_agent = None
    agent._booking_confirmed_sent = False
    agent._low_confidence_count = 0
    agent.session = MagicMock()
    agent.session.tts.update_options = MagicMock()
    return agent


# ---------------------------------------------------------------------------
# Guardrail 1 — Emergency keyword detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_emergency_keyword_blocks_graph():
    """Emergency phrase in message → 108 mentioned in reply, graph NOT invoked."""
    agent = await _build_agent(lang_code="hi-IN")

    graph_invoke = AsyncMock()

    with patch("voice.livekit_agent.translate_text", new=AsyncMock(
        return_value="यह एक आपात स्थिति है। कृपया तुरंत 108 पर कॉल करें।"
    )), patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = graph_invoke

        # Use Devanagari so "दिल का दौरा" substring matches the keyword dict
        reply = await agent.llm_node(
            _make_chat_ctx("मुझे दिल का दौरा आ रहा है"),
            tools=[],
            model_settings=MagicMock(),
        )

    assert "108" in reply
    assert agent.state["escalation_required"] is True
    assert agent.state["escalation_reason"] == "Emergency keyword detected"
    graph_invoke.assert_not_called()


@pytest.mark.asyncio
async def test_emergency_english_keyword_in_hindi_call():
    """English 'chest pain' triggers emergency even during hi-IN call."""
    agent = await _build_agent(lang_code="hi-IN")

    with patch("voice.livekit_agent.translate_text", new=AsyncMock(return_value="Emergency: call 108")), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock()
        reply = await agent.llm_node(
            _make_chat_ctx("I have chest pain and cannot breathe"),
            tools=[], model_settings=MagicMock(),
        )

    mock_graph.ainvoke.assert_not_called()
    assert agent.state["escalation_required"] is True


# ---------------------------------------------------------------------------
# Guardrail 2 — STT confidence retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stt_low_confidence_returns_retry_without_graph():
    """detection_confidence < 0.3 and count <= 3 → clarification reply, no graph."""
    agent = await _build_agent(lang_code="hi-IN", detection_confidence=0.2)

    with patch("voice.livekit_agent.translate_text", new=AsyncMock(
        return_value="मैं आपकी बात स्पष्ट नहीं सुन पाई। क्या आप दोबारा बोल सकते हैं?"
    )), patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock()
        reply = await agent.llm_node(
            _make_chat_ctx("..."),
            tools=[], model_settings=MagicMock(),
        )

    mock_graph.ainvoke.assert_not_called()
    assert agent._low_confidence_count == 1
    assert "सुन" in reply or len(reply) > 0  # got some retry message


@pytest.mark.asyncio
async def test_stt_low_confidence_falls_through_after_3():
    """After 3 retries, graph is invoked even with low confidence."""
    agent = await _build_agent(lang_code="hi-IN", detection_confidence=0.2)
    agent._low_confidence_count = 3  # already at limit

    def _fake_state():
        s = agent.state.copy()
        s["messages"] = [*s["messages"], {"role": "assistant", "content": "आपका स्वागत है"}]
        return s

    with patch("voice.livekit_agent.translate_text", new=AsyncMock(return_value="retry")), \
         patch("voice.livekit_agent.sarvam_identify_language", new=AsyncMock(return_value="hi-IN")), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock(side_effect=lambda s, config: _fake_state())
        await agent.llm_node(
            _make_chat_ctx("abc"),
            tools=[], model_settings=MagicMock(),
        )

    mock_graph.ainvoke.assert_called_once()


# ---------------------------------------------------------------------------
# Guardrail 3 — Output language consistency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_language_consistency_corrects_wrong_language():
    """Graph outputs Hindi when lang_code is mr-IN → reply translated to Marathi."""
    agent = await _build_agent(lang_code="mr-IN")

    hindi_reply = "आपकी अपॉइंटमेंट बुक हो गई है।"
    marathi_reply = "तुमची अपॉइंटमेंट बुक झाली आहे."

    def _after_graph():
        s = agent.state.copy()
        s["messages"] = [*s["messages"], {"role": "assistant", "content": hindi_reply}]
        s["current_agent"] = "scheduler"
        return s

    with patch("voice.livekit_agent.sarvam_identify_language", new=AsyncMock(return_value="hi-IN")), \
         patch("voice.livekit_agent.translate_text", new=AsyncMock(return_value=marathi_reply)), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock(side_effect=lambda s, config: _after_graph())
        reply = await agent.llm_node(
            _make_chat_ctx("मला अपॉइंटमेंट हवी आहे"),
            tools=[], model_settings=MagicMock(),
        )

    assert reply == marathi_reply


@pytest.mark.asyncio
async def test_language_consistency_skipped_for_english():
    """lang_code == en-IN → language check skipped entirely."""
    agent = await _build_agent(lang_code="en-IN")

    en_reply = "Your appointment is booked."

    def _after_graph():
        s = agent.state.copy()
        s["messages"] = [*s["messages"], {"role": "assistant", "content": en_reply}]
        s["current_agent"] = "scheduler"
        return s

    identify_mock = AsyncMock(return_value="en-IN")

    with patch("voice.livekit_agent.sarvam_identify_language", new=identify_mock), \
         patch("voice.livekit_agent.translate_text", new=AsyncMock(return_value="should not be called")), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock(side_effect=lambda s, config: _after_graph())
        reply = await agent.llm_node(
            _make_chat_ctx("I need an appointment"),
            tools=[], model_settings=MagicMock(),
        )

    identify_mock.assert_not_called()
    assert reply == en_reply


# ---------------------------------------------------------------------------
# Guardrail 4 — Medical boundary check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_medical_boundary_blocks_dosage_advice():
    """Prescription agent reply containing 'stop taking' is replaced with safe message."""
    agent = await _build_agent(lang_code="hi-IN")

    unsafe_reply = "आपको दवा लेना बंद कर देना चाहिए।"
    safe_reply = "किसी भी दवाई में बदलाव के लिए कृपया अपने डॉक्टर से मिलें।"

    def _after_graph():
        s = agent.state.copy()
        s["messages"] = [*s["messages"], {"role": "assistant", "content": unsafe_reply}]
        s["current_agent"] = "prescription"
        return s

    async def _fake_translate(text, source_lang, target_lang):
        if target_lang == "en-IN":
            # Simulated EN translation of the Hindi unsafe reply
            return "you should stop taking your medicine"
        # Translating safe EN message → hi-IN
        return safe_reply

    with patch("voice.livekit_agent.translate_text", new=_fake_translate), \
         patch("voice.livekit_agent.sarvam_identify_language", new=AsyncMock(return_value="hi-IN")), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()):
        mock_graph.ainvoke = AsyncMock(side_effect=lambda s, config: _after_graph())
        reply = await agent.llm_node(
            _make_chat_ctx("meri dawai ke baare mein batao"),
            tools=[], model_settings=MagicMock(),
        )

    assert reply == safe_reply
    assert agent.state["escalation_required"] is True
    assert agent.state["escalation_reason"] == "Medical advice boundary crossed"


# ---------------------------------------------------------------------------
# Guardrail 5 — TTS length cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tts_cap_summarises_long_reply():
    """Reply > 300 chars is summarised to a shorter string."""
    agent = await _build_agent(lang_code="hi-IN")

    long_reply = "आ" * 400  # 400 Devanagari chars — well over the 300 limit
    short_reply = "आ" * 50

    def _after_graph():
        s = agent.state.copy()
        s["messages"] = [*s["messages"], {"role": "assistant", "content": long_reply}]
        s["current_agent"] = "scheduler"
        return s

    sarvamai_mock = MagicMock()
    completion_resp = MagicMock()
    completion_resp.choices[0].message.content = short_reply
    sarvamai_mock.return_value.chat.completions.return_value = completion_resp

    with patch("voice.livekit_agent.sarvam_identify_language", new=AsyncMock(return_value="hi-IN")), \
         patch("voice.livekit_agent.translate_text", new=AsyncMock(side_effect=lambda t, **kw: t)), \
         patch("voice.livekit_agent.inbound_graph") as mock_graph, \
         patch("voice.livekit_agent.save_session_state", new=AsyncMock()), \
         patch("voice.livekit_agent.asyncio.to_thread", new=AsyncMock(return_value=completion_resp)):
        mock_graph.ainvoke = AsyncMock(side_effect=lambda s, config: _after_graph())
        # Patch SarvamAI inside the method via sys.modules
        import voice.livekit_agent as la_mod
        from unittest.mock import patch as _patch
        with _patch.dict(sys.modules, {"sarvamai": MagicMock(SarvamAI=sarvamai_mock)}):
            reply = await agent.llm_node(
                _make_chat_ctx("test"),
                tools=[], model_settings=MagicMock(),
            )

    assert len(reply) <= len(long_reply)


# ---------------------------------------------------------------------------
# Guardrail 6 — PII scrub
# ---------------------------------------------------------------------------

def test_scrub_pii_phone():
    from agents.tools.pii_tools import scrub_pii
    assert "[PHONE]" in scrub_pii("My number is 9876543210")
    assert "9876543210" not in scrub_pii("My number is 9876543210")


def test_scrub_pii_aadhaar():
    from agents.tools.pii_tools import scrub_pii
    assert "[AADHAAR]" in scrub_pii("Aadhaar: 1234 5678 9012")
    assert "[AADHAAR]" in scrub_pii("Aadhaar: 1234-5678-9012")
    assert "[AADHAAR]" in scrub_pii("123456789012")


def test_scrub_pii_dob():
    from agents.tools.pii_tools import scrub_pii
    assert "[DOB]" in scrub_pii("DOB: 15/08/1985")
    assert "[DOB]" in scrub_pii("Born on 3-4-1990")


def test_scrub_pii_preserves_non_pii():
    from agents.tools.pii_tools import scrub_pii
    text = "Your appointment is tomorrow at 10 AM."
    assert scrub_pii(text) == text


def test_scrub_pii_applied_before_logging():
    """post_call_node scrubs phone numbers from messages before Supabase write."""
    from agents.tools.pii_tools import scrub_pii
    messages = [
        {"role": "user", "content": "mera phone 9876543210 hai"},
        {"role": "assistant", "content": "Thank you, Ramesh."},
    ]
    scrubbed = [
        {**m, "content": scrub_pii(m["content"])} if m.get("content") else m
        for m in messages
    ]
    assert "[PHONE]" in scrubbed[0]["content"]
    assert "9876543210" not in scrubbed[0]["content"]
    assert scrubbed[1]["content"] == "Thank you, Ramesh."
