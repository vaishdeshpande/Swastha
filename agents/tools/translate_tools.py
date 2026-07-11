"""Sarvam Mayura v1 (translate) and language identification wrappers."""

import asyncio
import logging
import os

from langsmith import traceable
from sarvamai import SarvamAI

logger = logging.getLogger(__name__)

# Sync client — used via asyncio.to_thread so the blocking HTTP call runs in a
# thread-pool worker and never blocks the LiveKit event loop.
client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

# Hard ceiling per API call — a stalled translate/identify call must never
# hang a voice turn (field reports: 30-60s dead air). On timeout we degrade
# gracefully: translate returns the untranslated text, identify returns hi-IN.
API_TIMEOUT_S = 8.0


@traceable(run_type="tool", name="sarvam-mayura:translate")
async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using Sarvam Mayura v1.
    Primary use: doctor notes (en-IN) → patient language (hi-IN, mr-IN)."""
    if source_lang == target_lang:
        logger.debug("translate: source == target (%s), skipping API call", source_lang)
        return text
    logger.debug("translate: %s -> %s (%d chars)", source_lang, target_lang, len(text))

    def _sync() -> str:
        return client.text.translate(
            input=text,
            source_language_code=source_lang,
            target_language_code=target_lang,
        ).translated_text

    try:
        result = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=API_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.error("translate: timed out after %.0fs (%s -> %s) — returning original text",
                     API_TIMEOUT_S, source_lang, target_lang)
        return text
    logger.debug("translate: done (%d chars -> %d chars)", len(text), len(result))
    return result


@traceable(run_type="tool", name="sarvam:identify_language")
async def sarvam_identify_language(text: str) -> str:
    """Fallback language detection when STT confidence is low.
    Uses asyncio.to_thread so the blocking HTTP call doesn't stall the LiveKit
    event loop. Avoids AsyncSarvamAI whose httpx client can bind to the wrong
    event loop when initialised at module import time."""
    logger.debug("identify_language: running on %d chars", len(text))
    try:
        lang_code = await asyncio.wait_for(
            asyncio.to_thread(lambda: client.text.identify_language(input=text).language_code),
            timeout=API_TIMEOUT_S,
        )
        logger.info("identify_language: detected lang_code=%s", lang_code)
        return lang_code
    except Exception:
        logger.exception("identify_language: API call failed, defaulting to hi-IN")
        return "hi-IN"
