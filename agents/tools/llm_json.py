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

    repaired = _repair_truncated(text)
    if repaired is not None:
        logger.info("llm_json: recovered truncated JSON via repair")
        return repaired

    logger.warning("llm_json: could not extract JSON from LLM reply (%d chars)", len(text))
    return None


def _repair_truncated(text: str) -> dict | None:
    """Attempt to repair a JSON object cut off mid-stream (e.g. max_tokens hit
    or the model stopped early): close an open string, strip a trailing comma,
    and balance braces."""
    start = text.find("{")
    if start == -1:
        return None
    fragment = text[start:]

    # Close an unterminated string (count unescaped quotes)
    quote_count = len(re.findall(r'(?<!\\)"', fragment))
    if quote_count % 2 == 1:
        fragment += '"'

    fragment = re.sub(r",\s*$", "", fragment.rstrip())
    open_braces = fragment.count("{") - fragment.count("}")
    if open_braces > 0:
        fragment += "}" * open_braces

    try:
        result = json.loads(fragment)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


_REPLY_VALUE_RE = re.compile(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)


def extract_reply_text(text: str) -> str | None:
    """Last-resort salvage: pull the value of the "reply" key out of malformed
    JSON so raw JSON syntax is never spoken to the patient."""
    if not text:
        return None
    match = _REPLY_VALUE_RE.search(text)
    if not match:
        return None
    value = match.group(1)
    try:
        value = json.loads(f'"{value}"')  # unescape \n, \", unicode escapes
    except json.JSONDecodeError:
        pass
    value = value.strip()
    return value or None
