"""Agent 1 — Language Router.

Always runs first. Detects language, sets voice persona. No DB calls, no LLM.

Language stability — hysteresis filter:
  Saaras v3 returns detected_language on every turn at zero extra cost.
  A single code-mixed word must not flip the entire session's language.
  We only switch when the STT-detected language differs from current lang_code
  for 2 CONSECUTIVE turns (lang_mismatch_count >= 2). Any matching turn resets
  the counter. This means:
    - One Hinglish word in a Marathi sentence → ignored.
    - Patient genuinely switches to Hindi for 2+ turns → voice switches.
"""

import logging

from agents.state import AgentState
from agents.tools.language_config import load_language_config
from agents.tools.redis_tools import redis_get
from agents.tools.translate_tools import sarvam_identify_language

logger = logging.getLogger(__name__)

LOW_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_DETECTED_LANG = "hi-IN"
LANG_SWITCH_THRESHOLD = 2   # consecutive mismatching turns required to switch language


async def language_router_node(state: AgentState) -> AgentState:
    state["current_agent"] = "language_router"
    call_id = state.get("call_id")
    logger.info("language_router: start (call_id=%s)", call_id)

    current_lang = state.get("lang_code", DEFAULT_DETECTED_LANG)
    mismatch_count = state.get("lang_mismatch_count", 0)

    # 1. Check Upstash Redis for cached language preference (repeat callers
    #    skip detection entirely — not just this turn, every turn).
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
            "lang_mismatch_count": 0,
        }

    logger.debug("language_router: cache miss for patient_id=%s", patient_id)

    # 2. Read detected_language from STT metadata (already on state, stamped by
    #    livekit_agent._on_user_input_transcribed before this node runs).
    #    Only trust concrete language codes — Sarvam streaming returns "unknown"
    #    as a placeholder which is not a real language.
    _raw = state.get("detected_language") or ""
    _is_concrete = bool(_raw) and _raw != "unknown"
    detected_lang = _raw if _is_concrete else None

    # 3. Fallback: if STT confidence is low / missing or detected_language is
    #    absent, call Sarvam Language ID API on the latest utterance.
    #    When the identify API is used, the result is high-confidence — skip hysteresis.
    detection_confidence = state.get("detection_confidence") if _is_concrete else None
    used_identify_api = False
    # Call identify API when: no detected language, confidence unknown (None), or confidence < threshold
    if detected_lang is None or detection_confidence is None or detection_confidence < LOW_CONFIDENCE_THRESHOLD:
        if state.get("messages"):
            logger.info(
                "language_router: low/missing STT confidence (%s, raw=%r) — calling sarvam_identify_language",
                detection_confidence, _raw,
            )
            detected_lang = await sarvam_identify_language(state["messages"][-1]["content"])
            used_identify_api = True
            logger.info("language_router: sarvam_identify_language resolved lang_code=%s", detected_lang)
        else:
            detected_lang = current_lang  # no utterance yet — keep current

    # 4. Hysteresis filter — only switch language after LANG_SWITCH_THRESHOLD
    #    consecutive turns where the detected language differs from the current one.
    #    Skip hysteresis when the identify API gave us a definitive result.
    if used_identify_api and detected_lang != current_lang:
        new_lang = detected_lang
        new_mismatch = 0
        logger.info("language_router: identify API returned %s (skipping hysteresis)", detected_lang)
    elif detected_lang == current_lang:
        # Matching turn — reset mismatch counter, no language change
        new_mismatch = 0
        new_lang = current_lang
        logger.debug("language_router: detected=%s matches current=%s, mismatch_count reset", detected_lang, current_lang)
    else:
        new_mismatch = mismatch_count + 1
        if new_mismatch >= LANG_SWITCH_THRESHOLD:
            # Threshold reached — switch language
            new_lang = detected_lang
            new_mismatch = 0
            logger.info(
                "language_router: language switch confirmed after %d consecutive mismatches: %s → %s",
                LANG_SWITCH_THRESHOLD, current_lang, new_lang,
            )
        else:
            # One mismatch — hold current language, wait for next turn
            new_lang = current_lang
            logger.info(
                "language_router: detected=%s ≠ current=%s, mismatch_count=%d/%d — holding current",
                detected_lang, current_lang, new_mismatch, LANG_SWITCH_THRESHOLD,
            )

    lang_config = load_language_config(new_lang)
    logger.info("language_router: final lang_code=%s tts_voice=%s", new_lang, lang_config["tts_voice"])

    return {
        **state,
        "lang_code": new_lang,
        "tts_voice": lang_config["tts_voice"],
        "tts_model": lang_config["tts_model"],
        "lang_mismatch_count": new_mismatch,
    }
