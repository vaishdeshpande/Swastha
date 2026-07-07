"""Sarvam Mayura v1 (translate) and language identification wrappers."""

import logging
import os

from sarvamai import SarvamAI

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])


async def translate_text(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text using Sarvam Mayura v1.
    Primary use: doctor notes (en-IN) → patient language (hi-IN, mr-IN)."""
    if source_lang == target_lang:
        logger.debug("translate: source == target (%s), skipping API call", source_lang)
        return text
    logger.debug("translate: %s -> %s (%d chars)", source_lang, target_lang, len(text))
    response = client.text.translate(
        input=text,
        source_language_code=source_lang,
        target_language_code=target_lang,
    )
    result = response.translated_text
    logger.debug("translate: done (%d chars -> %d chars)", len(text), len(result))
    return result


async def sarvam_identify_language(text: str) -> str:
    """Fallback language detection when STT confidence is low.
    Used by Agent 1 (Language Router)."""
    logger.debug("identify_language: running on %d chars", len(text))
    response = client.text.identify_language(input=text)
    lang_code = response.language_code
    logger.info("identify_language: detected lang_code=%s", lang_code)
    return lang_code
