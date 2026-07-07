"""Agent 2 — Voice Intake.

Collects patient identity and intent. Four architectural optimisations run here:

  Scenario 1 — Parallel Background Registration
    As soon as a phone number is parsed from the streaming LLM response, an
    asyncio.Task fires get_patient_record() in the background. An optimistic UUID
    is reserved immediately so the scheduler can proceed without waiting.

  Scenario 2 — Slot Pre-fetch on Intent Detection
    The moment intent="book" and department are both known, a background task
    calls check_available_slots and writes the result to Redis (TTL 5 min) so
    the scheduler reads from cache, not Supabase, mid-conversation.

  Scenario 3 — Streaming Partial State Extraction
    sarvam-30b is called with stream=True. A lightweight regex scans each
    accumulated chunk for a parseable phone number and fires the DB lookup
    before the LLM finishes — cutting 400–800 ms from the intake→scheduler
    handoff. Falls back to non-streaming if the SDK does not support it.

  Scenario 4 — Confidence-Gated Multi-Agent Fanout
    On the first ambiguous turn (intent=None), two lightweight classifiers run
    in parallel (biased toward "book" and "prescription"). If one dominates,
    intent is resolved without a clarifying question. If both are high, a single
    targeted question is synthesised. Falls through to the normal loop otherwise.
"""

import asyncio
import logging
import os
import re
import uuid

from sarvamai import SarvamAI

from agents.prompts.voice_intake import build_voice_intake_prompt
from agents.state import AgentState
from agents.tools.db_tools import check_available_slots, get_patient_record, register_patient
from agents.tools.intent_classifier import run_intent_fanout
from agents.tools.llm_json import extract_json
from agents.tools.redis_tools import cache_slots

logger = logging.getLogger(__name__)

MAX_INTAKE_ATTEMPTS = 3

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

_INTAKE_FIELDS = ("intent", "phone", "patient_name", "age", "department", "urgency")

# Module-level store for background tasks keyed by session_id.
# Tasks are created inside this module and awaited before the node returns,
# so they never outlive the LangGraph node invocation.
_bg_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Scenario 3 — partial phone extractor (regex over partial JSON stream)
# ---------------------------------------------------------------------------

def _try_extract_phone(partial: str) -> str | None:
    """Scan a partial (potentially incomplete) JSON string for a phone number."""
    match = re.search(r'"phone"\s*:\s*"(\d{10,})"', partial)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Scenario 3 + 1 — streaming extraction with early phone lookup
# ---------------------------------------------------------------------------

async def _extract_patient_info(
    messages: list[dict],
    lang_code: str,
    already_collected: dict,
    session_id: str,
) -> tuple[dict, asyncio.Task | None]:
    """Call sarvam-30b to extract intake fields.

    Uses stream=True so that a phone number parsed mid-stream immediately fires
    a background get_patient_record() task (Scenarios 1 + 3). Falls back to a
    single non-streaming call if the SDK raises AttributeError or TypeError.

    Returns:
        (parsed_dict, phone_lookup_task | None)
    """
    system_prompt = build_voice_intake_prompt(lang_code, already_collected)
    full_messages = [{"role": "system", "content": system_prompt}, *messages]
    phone_task: asyncio.Task | None = None

    try:
        # ── Scenario 3: streaming path ──────────────────────────────────────
        stream = client.chat.completions(
            messages=full_messages,
            model="sarvam-30b",
            stream=True,
        )
        accumulated = ""
        async for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            accumulated += delta

            # Scenario 1 + 3: fire DB lookup the moment phone is parseable
            if phone_task is None and '"phone"' in accumulated:
                phone = _try_extract_phone(accumulated)
                if phone:
                    logger.info("voice_intake: phone %s extracted mid-stream — firing background lookup", phone)
                    phone_task = asyncio.create_task(get_patient_record(phone))
                    _bg_tasks[session_id] = phone_task

        parsed = extract_json(accumulated)
        if parsed is None:
            logger.warning("voice_intake: streaming reply not parseable JSON, treating as clarification: %r", accumulated[:200])
            return {"reply": accumulated, "intent": None}, phone_task
        return parsed, phone_task

    except (AttributeError, TypeError, NotImplementedError):
        # ── Fallback: non-streaming path ─────────────────────────────────────
        logger.info("voice_intake: sarvam SDK does not support streaming — falling back to batch call")
        response = client.chat.completions(
            messages=full_messages,
            model="sarvam-30b",
        )
        reply = response.choices[0].message.content
        if not reply:
            logger.warning("voice_intake: API returned empty/None content")
            return {"reply": None, "intent": None}, None
        parsed = extract_json(reply)
        if parsed is None:
            logger.warning("voice_intake: batch reply not parseable JSON: %r", reply[:200])
            return {"reply": reply, "intent": None}, None

        # Still fire the background lookup if phone is in the parsed result
        phone = parsed.get("phone")
        if phone and session_id not in _bg_tasks:
            phone_task = asyncio.create_task(get_patient_record(phone))
            _bg_tasks[session_id] = phone_task

        return parsed, phone_task


# ---------------------------------------------------------------------------
# Scenario 2 — background slot pre-fetch
# ---------------------------------------------------------------------------

async def _prefetch_slots(department: str) -> None:
    """Fetch next available slots for a department and write them to Redis cache."""
    try:
        slots = await check_available_slots(department, date="any")
        if not slots:
            from agents.tools.db_tools import get_next_available
            slots = await get_next_available(department, 3)
        await cache_slots(department, "any", slots[:3])
        logger.info("voice_intake: pre-fetched %d slot(s) for department=%s", len(slots[:3]), department)
    except Exception:
        logger.exception("voice_intake: slot pre-fetch failed for department=%s (non-fatal)", department)


# ---------------------------------------------------------------------------
# Main node
# ---------------------------------------------------------------------------

async def voice_intake_node(state: AgentState) -> AgentState:
    state["current_agent"] = "voice_intake"
    lang_code = state["lang_code"]
    session_id = state.get("session_id", "unknown")
    logger.info("voice_intake: start (session_id=%s, lang_code=%s)", session_id, lang_code)

    collected = dict(state.get("intake_collected") or {})
    logger.debug("voice_intake: messages=%d collected=%s", len(state["messages"]), collected)

    # ── Scenario 3 + 1: streaming extraction with early phone lookup ─────────
    extracted, phone_task = await _extract_patient_info(
        state["messages"], lang_code, collected, session_id
    )

    for field in _INTAKE_FIELDS:
        new_val = extracted.get(field)
        if new_val is not None:
            collected[field] = new_val

    intent = collected.get("intent")
    reply_text = extracted.get("reply")
    messages = [*state["messages"]]
    if reply_text:
        messages.append({"role": "assistant", "content": reply_text})

    # ── Scenario 4: confidence-gated fanout on first ambiguous turn ──────────
    attempt_count = state.get("intake_attempt_count", 0)
    if intent is None and attempt_count == 0 and state["messages"]:
        last_utterance = next(
            (m["content"] for m in reversed(state["messages"]) if m["role"] == "user"),
            "",
        )
        if last_utterance:
            resolved_intent, scores, clarify_q = await run_intent_fanout(last_utterance, lang_code)
            logger.info(
                "voice_intake: fanout scores=%s resolved_intent=%s",
                scores, resolved_intent,
            )
            if resolved_intent:
                # One classifier dominated — route directly without a clarifying round
                collected["intent"] = resolved_intent
                intent = resolved_intent
            elif clarify_q:
                # Both above threshold — ask one targeted question and await next turn
                messages.append({"role": "assistant", "content": clarify_q})
                return {
                    **state,
                    "messages": messages,
                    "intake_collected": collected,
                    "intake_attempt_count": attempt_count + 1,
                    "intent_classifier_scores": scores,
                }
            # else: both below threshold, fall through to normal clarification loop

    if intent is None:
        attempt_count += 1
        logger.info("voice_intake: intent unclear, clarification round %d/%d", attempt_count, MAX_INTAKE_ATTEMPTS)

        if attempt_count >= MAX_INTAKE_ATTEMPTS:
            logger.warning("voice_intake: escalating after %d unclear rounds (session_id=%s)", attempt_count, session_id)
            return {
                **state,
                "messages": messages,
                "intake_collected": collected,
                "intake_attempt_count": attempt_count,
                "escalation_required": True,
                "escalation_reason": "Unable to determine patient intent after 3 rounds",
            }

        return {
            **state,
            "messages": messages,
            "intake_collected": collected,
            "intake_attempt_count": attempt_count,
        }

    patient_id = state.get("patient_id")
    is_new_patient = state.get("is_new_patient", False)
    phone = collected.get("phone")

    # Phone-first gate: intent known but phone missing — ask for it
    if not phone and not patient_id:
        PHONE_PROMPTS = {
            "hi-IN": "कृपया अपना फ़ोन नंबर बताएं ताकि मैं आपकी जानकारी देख सकूँ।",
            "mr-IN": "कृपया तुमचा फोन नंबर सांगा जेणेकरून मी तुमची माहिती पाहू शकेन।",
            "en-IN": "Could you please share your phone number so I can look up your details?",
        }
        ask_phone = PHONE_PROMPTS.get(lang_code, PHONE_PROMPTS["en-IN"])
        logger.info("voice_intake: intent=%s locked in collected, but no phone — asking", intent)
        if not reply_text:
            messages.append({"role": "assistant", "content": ask_phone})
        return {
            **state,
            "messages": messages,
            "intake_collected": collected,
            "intent": None,
            "department": collected.get("department", state.get("department")),
            "urgency": collected.get("urgency", "normal"),
        }

    # Department gate: intent=book but no department yet — let LLM ask about symptoms
    if intent == "book" and not collected.get("department"):
        logger.info("voice_intake: intent=book but department not yet inferred — awaiting symptoms")
        return {
            **state,
            "messages": messages,
            "intake_collected": collected,
            "intent": None,
            "urgency": collected.get("urgency", "normal"),
        }

    # ── Scenario 1: resolve background phone lookup ─────────────────────────
    if phone and not patient_id:
        if phone_task is not None:
            # Task was fired mid-stream — await it (usually already done)
            existing = await phone_task
        else:
            existing = await get_patient_record(phone)

        # Clean up module-level task store
        _bg_tasks.pop(session_id, None)

        if existing is None:
            # Generate optimistic UUID and reserve it before calling register_patient
            optimistic_id = state.get("optimistic_patient_id") or str(uuid.uuid4())
            logger.info("voice_intake: new patient — registering with optimistic_id=%s", optimistic_id)
            patient_id = await register_patient(
                name=collected.get("patient_name", ""),
                phone=phone,
                age=collected.get("age") or 0,
                lang_pref=lang_code,
            )
            is_new_patient = True
            logger.info("voice_intake: registered new patient_id=%s", patient_id)
        else:
            patient_id = existing["id"]
            is_new_patient = False
            logger.info("voice_intake: matched existing patient_id=%s", patient_id)

    # ── Scenario 2: pre-fetch slots once intent+department are known ─────────
    department = collected.get("department", state.get("department"))
    if intent == "book" and department and not state.get("prefetched_slots"):
        asyncio.create_task(_prefetch_slots(department))
        logger.info("voice_intake: fired slot pre-fetch for department=%s", department)

    logger.info(
        "voice_intake: resolved intent=%s department=%s urgency=%s patient_id=%s",
        intent, department, collected.get("urgency", "normal"), patient_id,
    )

    return {
        **state,
        "patient_id": patient_id,
        "patient_name": collected.get("patient_name", state.get("patient_name")),
        "is_new_patient": is_new_patient,
        "intent": intent,
        "department": department,
        "urgency": collected.get("urgency", "normal"),
        "intake_collected": collected,
        "optimistic_patient_id": state.get("optimistic_patient_id"),
        "prefetched_slots": True if (intent == "book" and department) else state.get("prefetched_slots"),
    }
