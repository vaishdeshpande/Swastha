"""Tests for Agent 1 — Language Router.

Patches:
- redis_get: controls cache hit / miss
- sarvam_identify_language: controls API fallback
- load_language_config: returns deterministic config dicts
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import make_state


HINDI_CONFIG = {"tts_voice": "priya", "tts_model": "bulbul:v3", "name": "Hindi", "enabled": True}
MARATHI_CONFIG = {"tts_voice": "kavya", "tts_model": "bulbul:v3", "name": "Marathi", "enabled": True}


@pytest.mark.asyncio
async def test_cache_hit_skips_detection():
    """When Redis has a cached lang_pref, the agent returns it immediately
    and never calls sarvam_identify_language."""
    state = make_state(patient_id="p1", detection_confidence=None)

    with (
        patch("agents.agent_language_router.redis_get", AsyncMock(return_value="mr-IN")),
        patch("agents.agent_language_router.load_language_config", return_value=MARATHI_CONFIG),
        patch("agents.agent_language_router.sarvam_identify_language") as mock_identify,
    ):
        from agents.agent_language_router import language_router_node
        result = await language_router_node(state)

    mock_identify.assert_not_called()
    assert result["lang_code"] == "mr-IN"
    assert result["tts_voice"] == "kavya"
    assert result["current_agent"] == "language_router"


@pytest.mark.asyncio
async def test_cache_miss_uses_stt_detected_language():
    """No cache + high STT confidence: use detected_language directly,
    no call to sarvam_identify_language."""
    state = make_state(
        patient_id="p2",
        detected_language="hi-IN",
        detection_confidence=0.95,
    )

    with (
        patch("agents.agent_language_router.redis_get", AsyncMock(return_value=None)),
        patch("agents.agent_language_router.load_language_config", return_value=HINDI_CONFIG),
        patch("agents.agent_language_router.sarvam_identify_language") as mock_identify,
    ):
        from agents.agent_language_router import language_router_node
        result = await language_router_node(state)

    mock_identify.assert_not_called()
    assert result["lang_code"] == "hi-IN"
    assert result["tts_voice"] == "priya"


@pytest.mark.asyncio
async def test_low_confidence_falls_back_to_identify_api():
    """Confidence below threshold triggers sarvam_identify_language API."""
    state = make_state(
        patient_id="p3",
        detected_language="hi-IN",
        detection_confidence=0.4,  # below LOW_CONFIDENCE_THRESHOLD=0.6
        messages=[{"role": "user", "content": "Namaste mujhe doctor se milna hai"}],
    )

    with (
        patch("agents.agent_language_router.redis_get", AsyncMock(return_value=None)),
        patch("agents.agent_language_router.sarvam_identify_language", AsyncMock(return_value="hi-IN")),
        patch("agents.agent_language_router.load_language_config", return_value=HINDI_CONFIG),
    ):
        from agents.agent_language_router import language_router_node
        result = await language_router_node(state)

    assert result["lang_code"] == "hi-IN"


@pytest.mark.asyncio
async def test_none_confidence_falls_back_to_identify_api():
    """detection_confidence=None (key present but None) must not raise TypeError."""
    state = make_state(
        patient_id="p4",
        detected_language=None,
        detection_confidence=None,  # the bug case — None is not < 0.6
        messages=[{"role": "user", "content": "नमस्ते"}],
    )

    with (
        patch("agents.agent_language_router.redis_get", AsyncMock(return_value=None)),
        patch("agents.agent_language_router.sarvam_identify_language", AsyncMock(return_value="hi-IN")),
        patch("agents.agent_language_router.load_language_config", return_value=HINDI_CONFIG),
    ):
        from agents.agent_language_router import language_router_node
        result = await language_router_node(state)

    assert result["lang_code"] == "hi-IN"


@pytest.mark.asyncio
async def test_unknown_patient_uses_unknown_key_for_cache_lookup():
    """When patient_id is None, the cache key should be lang_pref:unknown."""
    state = make_state(patient_id=None, detected_language="hi-IN", detection_confidence=0.9)

    mock_redis_get = AsyncMock(return_value=None)
    with (
        patch("agents.agent_language_router.redis_get", mock_redis_get),
        patch("agents.agent_language_router.load_language_config", return_value=HINDI_CONFIG),
    ):
        from agents.agent_language_router import language_router_node
        await language_router_node(state)

    mock_redis_get.assert_called_once_with("lang_pref:unknown")
