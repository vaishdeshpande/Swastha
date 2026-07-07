"""Scenario 4 — Confidence-Gated Multi-Agent Fanout.

When voice_intake gets an ambiguous first utterance, two lightweight sarvam-30b
classifiers run in parallel (one biased toward "book", one toward "prescription").
Their confidence scores decide whether to route directly or ask one targeted
clarifying question — preventing the escalation_required fallback from firing too early.
"""

import asyncio
import logging
import os
import re
from typing import Literal

from sarvamai import SarvamAI

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

CONFIDENCE_THRESHOLD = 0.65

_BOOK_BIAS_PROMPT = """\
You are a classifier. The patient said something that MAY be a request to book a hospital appointment.
Score how likely it is (0.0 = definitely not, 1.0 = definitely yes).
Reply with ONLY a JSON object: {"confidence": <float>}
Patient utterance: {text}"""

_PRESCRIPTION_BIAS_PROMPT = """\
You are a classifier. The patient said something that MAY be a request about their prescription or medicines.
Score how likely it is (0.0 = definitely not, 1.0 = definitely yes).
Reply with ONLY a JSON object: {"confidence": <float>}
Patient utterance: {text}"""

_CLARIFY_QUESTION: dict[str, str] = {
    "hi-IN": "क्या आप नया अपॉइंटमेंट बुक करना चाहते हैं, या अपनी मौजूदा दवाइयों के बारे में जानना चाहते हैं?",
    "mr-IN": "तुम्हाला नवीन अपॉइंटमेंट बुक करायचे आहे का, की विद्यमान औषधांबद्दल माहिती हवी आहे?",
    "en-IN": "Would you like to book a new appointment, or check on your existing medicines?",
}


async def _classify_with_bias(text: str, bias: Literal["book", "prescription"]) -> float:
    """Run a single lightweight sarvam-30b call biased toward one intent.
    Returns confidence in [0.0, 1.0]. Returns 0.0 on any failure."""
    prompt = (_BOOK_BIAS_PROMPT if bias == "book" else _PRESCRIPTION_BIAS_PROMPT).format(text=text)
    try:
        response = client.chat.completions(
            messages=[{"role": "user", "content": prompt}],
            model="sarvam-30b",
        )
        content = response.choices[0].message.content or ""
        match = re.search(r'"confidence"\s*:\s*([0-9.]+)', content)
        if match:
            score = float(match.group(1))
            logger.debug("intent_classifier: bias=%s text=%r -> score=%.3f", bias, text[:60], score)
            return min(max(score, 0.0), 1.0)
    except Exception:
        logger.exception("intent_classifier: classification failed for bias=%s", bias)
    return 0.0


async def run_intent_fanout(
    text: str,
    lang_code: str,
) -> tuple[str | None, dict[str, float], str | None]:
    """Run book + prescription classifiers in parallel.

    Returns:
        (resolved_intent, scores, clarifying_question)
        - resolved_intent: "book" | "prescription" | None
          None means both classifiers were inconclusive — caller should fall through
          to the normal clarification loop.
        - scores: {"book": float, "prescription": float}
        - clarifying_question: the question to ask when both scores >= threshold,
          else None.
    """
    book_score, rx_score = await asyncio.gather(
        _classify_with_bias(text, "book"),
        _classify_with_bias(text, "prescription"),
    )
    scores = {"book": book_score, "prescription": rx_score}
    logger.info("intent_fanout: book=%.3f prescription=%.3f (threshold=%.2f)", book_score, rx_score, CONFIDENCE_THRESHOLD)

    book_high = book_score >= CONFIDENCE_THRESHOLD
    rx_high = rx_score >= CONFIDENCE_THRESHOLD

    if book_high and not rx_high:
        return "book", scores, None
    if rx_high and not book_high:
        return "prescription", scores, None
    if book_high and rx_high:
        # Both confident — synthesize one clarifying question covering both options.
        question = _CLARIFY_QUESTION.get(lang_code, _CLARIFY_QUESTION["en-IN"])
        return None, scores, question
    # Both below threshold — let the normal clarification loop handle it.
    return None, scores, None
