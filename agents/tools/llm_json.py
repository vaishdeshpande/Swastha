"""Robust JSON extraction from LLM replies.

sarvam-30b doesn't reliably return pure JSON even when instructed to —
in practice it often wraps the object in conversational text and a
markdown ```json code fence. Every agent that asks the model for a
structured decision needs to tolerate that, so the extraction logic
lives here once instead of being copy-pasted per agent.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_FIRST_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_json(text: str) -> dict | None:
    """Best-effort parse of a JSON object out of a raw LLM reply.
    Returns None if no valid JSON object could be found."""
    if not text:
        return None

    for candidate in (text, *_CODE_FENCE_RE.findall(text)):
        try:
            result = json.loads(candidate)
            if candidate != text:
                logger.debug("llm_json: extracted JSON from code fence")
            return result
        except (json.JSONDecodeError, TypeError):
            continue

    match = _FIRST_OBJECT_RE.search(text)
    if match:
        try:
            result = json.loads(match.group(0))
            logger.debug("llm_json: extracted JSON via first-object regex")
            return result
        except json.JSONDecodeError:
            pass

    logger.warning("llm_json: could not extract JSON from LLM reply (%d chars)", len(text))
    return None
