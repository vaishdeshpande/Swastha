"""Scenario 4 — Confidence-Gated Multi-Agent Fanout.

When voice_intake gets an ambiguous first utterance, a single sarvam-30b call
scores all 6 intents in parallel. The highest-scoring intent above CONFIDENCE_THRESHOLD
is returned directly, saving a clarification round. If two or more intents tie above
the threshold, a targeted question covering both is synthesised.
"""

import logging
import os
import re

from sarvamai import SarvamAI

logger = logging.getLogger(__name__)

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

CONFIDENCE_THRESHOLD = 0.60

_MULTI_INTENT_PROMPT = """\
You are a hospital call-centre classifier. Score how likely the patient utterance belongs to each intent.
Return ONLY a JSON object with float scores 0.0–1.0 for all six intents. No explanation, no markdown.

Intents:
  book        — wants to book / reschedule / cancel a doctor appointment
  prescription — wants to know about their medicines or prescription
  lab         — wants to check lab / blood-test report status ("report aayi kya", "result chahiye")
  billing     — wants to know their bill amount or make a payment ("bill kitna hai", "payment karna hai")
  followup    — calling after discharge to follow up on their condition
  query       — general question (hospital timings, directions, phone number, etc.)

Reply format (JSON only):
{{"book": 0.0, "prescription": 0.0, "lab": 0.0, "billing": 0.0, "followup": 0.0, "query": 0.0}}

Patient utterance: {text}"""

_CLARIFY_TEMPLATES: dict[str, dict[str, str]] = {
    "book+prescription": {
        "hi-IN": "क्या आप नया अपॉइंटमेंट बुक करना चाहते हैं, या अपनी मौजूदा दवाइयों के बारे में जानना चाहते हैं?",
        "mr-IN": "तुम्हाला नवीन अपॉइंटमेंट बुक करायचे आहे का, की औषधांबद्दल माहिती हवी आहे?",
        "en-IN": "Would you like to book an appointment, or check on your medicines?",
    },
    "book+lab": {
        "hi-IN": "क्या आप नया अपॉइंटमेंट बुक करना चाहते हैं, या अपनी lab report check करनी है?",
        "mr-IN": "तुम्हाला अपॉइंटमेंट बुक करायची आहे का, की लॅब रिपोर्ट तपासायचा आहे?",
        "en-IN": "Would you like to book an appointment, or check your lab report?",
    },
    "book+billing": {
        "hi-IN": "क्या आप appointment book करना चाहते हैं, या bill के बारे में पूछना है?",
        "mr-IN": "तुम्हाला अपॉइंटमेंट बुक करायची आहे का, की बिलाबद्दल विचारायचे आहे?",
        "en-IN": "Would you like to book an appointment, or ask about your bill?",
    },
    "prescription+lab": {
        "hi-IN": "क्या आप अपनी दवाइयों के बारे में पूछना चाहते हैं, या lab report check करनी है?",
        "mr-IN": "तुम्हाला औषधांबद्दल विचारायचे आहे का, की लॅब रिपोर्ट बघायचा आहे?",
        "en-IN": "Would you like to ask about your medicines, or check your lab report?",
    },
    "default": {
        "hi-IN": "आप doctor से appointment लेना चाहते हैं, दवाई के बारे में पूछना है, report check करनी है, या bill देखना है?",
        "mr-IN": "तुम्हाला अपॉइंटमेंट हवी आहे, औषधांबद्दल विचारायचे आहे, रिपोर्ट बघायचा आहे, की बिल बघायचे आहे?",
        "en-IN": "Are you looking to book an appointment, check medicines, view a lab report, or pay a bill?",
    },
}

_ALL_INTENTS = ("book", "prescription", "lab", "billing", "followup", "query")


def _clarify_key(top_two: list[str]) -> str:
    pair = "+".join(sorted(top_two[:2]))
    return pair if pair in _CLARIFY_TEMPLATES else "default"


async def run_intent_fanout(
    text: str,
    lang_code: str,
) -> tuple[str | None, dict[str, float], str | None]:
    """Score all 6 intents with a single sarvam-30b call.

    Returns:
        (resolved_intent, scores, clarifying_question)
        - resolved_intent: the winning intent string, or None if ambiguous
        - scores: dict of all 6 intent scores
        - clarifying_question: targeted question when two intents tie, else None
    """
    prompt = _MULTI_INTENT_PROMPT.format(text=text)
    scores: dict[str, float] = {k: 0.0 for k in _ALL_INTENTS}

    try:
        response = client.chat.completions(
            messages=[{"role": "user", "content": prompt}],
            model="sarvam-30b",
        )
        content = response.choices[0].message.content or ""
        for intent in _ALL_INTENTS:
            match = re.search(rf'"{intent}"\s*:\s*([0-9.]+)', content)
            if match:
                scores[intent] = min(max(float(match.group(1)), 0.0), 1.0)
    except Exception:
        logger.exception("intent_classifier: fanout call failed")
        return None, scores, None

    logger.info("intent_fanout scores=%s (threshold=%.2f)", scores, CONFIDENCE_THRESHOLD)

    above = sorted(
        [(k, v) for k, v in scores.items() if v >= CONFIDENCE_THRESHOLD],
        key=lambda x: x[1],
        reverse=True,
    )

    if not above:
        return None, scores, None

    if len(above) == 1:
        return above[0][0], scores, None

    # Two or more above threshold — check if top two are clearly separated
    top_intent, top_score = above[0]
    second_intent, second_score = above[1]

    if top_score - second_score >= 0.15:
        # Clear winner
        return top_intent, scores, None

    # Too close — ask one targeted clarifying question
    key = _clarify_key([top_intent, second_intent])
    question = _CLARIFY_TEMPLATES[key].get(lang_code, _CLARIFY_TEMPLATES[key]["en-IN"])
    return None, scores, question
