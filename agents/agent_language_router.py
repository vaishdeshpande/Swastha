"""Agent 1 — Language Router.

Always runs first. Detects language, sets voice persona. No DB calls, no LLM.
"""

import logging

from agents.state import AgentState
from agents.tools.language_config import load_language_config
from agents.tools.redis_tools import redis_get
from agents.tools.translate_tools import sarvam_identify_language

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_DETECTED_LANG = "hi-IN"


async def language_router_node(state: AgentState) -> AgentState:
    state["current_agent"] = "language_router"
    call_id = state.get("call_id")
    logger.info("language_router: start (call_id=%s)", call_id)

    # 1. Check Upstash Redis for cached language preference (repeat callers
    #    skip detection entirely).
    patient_id = state.get("patient_id") or "unknown"
    cached_lang = await redis_get(f"lang_pref:{patient_id}")
    if cached_lang:
        logger.info("language_router: cache hit for patient_id=%s -> lang_code=%s", patient_id, cached_lang)
        lang_config = load_language_config(cached_lang)
        return {
            **state,
            "lang_code": cached_lang,
            "tts_voice": lang_config["tts_voice"],
            "tts_model": lang_config["tts_model"],
        }

    logger.debug("language_router: cache miss for patient_id=%s", patient_id)

    # 2. If no cache, STT auto-detects from first utterance. Saaras v3 with
    #    language="unknown" returns detected_language in response metadata —
    #    the LiveKit agent session reads this and stores it on state before
    #    invoking the graph.
    detected_lang = state.get("detected_language") or DEFAULT_DETECTED_LANG

    # 3. Fallback: if STT detection confidence is low (or wasn't reported at
    #    all, e.g. detected_language came from a plain text message with no
    #    STT metadata), call Sarvam Language ID API on the latest utterance.
    detection_confidence = state.get("detection_confidence")
    if detection_confidence is None or detection_confidence < LOW_CONFIDENCE_THRESHOLD:
        logger.info(
            "language_router: low/missing STT confidence (%s) — falling back to sarvam_identify_language",
            detection_confidence,
        )
        detected_lang = await sarvam_identify_language(state["messages"][-1]["content"])
        logger.info("language_router: sarvam_identify_language resolved lang_code=%s", detected_lang)

    # 4. Load language config from languages.yaml
    lang_config = load_language_config(detected_lang)
    logger.info("language_router: resolved lang_code=%s tts_voice=%s", detected_lang, lang_config["tts_voice"])

    return {
        **state,
        "lang_code": detected_lang,
        "tts_voice": lang_config["tts_voice"],
        "tts_model": lang_config["tts_model"],
    }
