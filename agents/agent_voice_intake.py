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

from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from sarvamai import SarvamAI

from agents.prompts.voice_intake import build_voice_intake_prompt
from agents.state import AgentState
from agents.tools.db_tools import check_available_slots, get_hospital_availability, get_patient_record, register_patient
from agents.tools.intent_classifier import run_intent_fanout
from agents.tools.llm_json import extract_json, extract_reply_text
from agents.tools.redis_tools import cache_slots
from agents.tools.translate_tools import translate_text

logger = logging.getLogger(__name__)

# Shared sync client — reused across turns (same pattern as the scheduler and
# prescription agents). Tests patch this attribute to mock the LLM.
client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

MAX_INTAKE_ATTEMPTS = 3

# Hard ceiling on any single sarvam-30b call. Measured latency is 2-14s;
# beyond this the patient hears dead air — better to apologise and re-ask
# than hang the turn (Scenario 2/3 field reports: 30-60s silences).
LLM_TIMEOUT_S = 20.0

# Sentinel returned when the LLM is unrecoverable (double timeout or double
# JSON parse failure). livekit_agent.py intercepts this flag and plays a
# pre-recorded fallback WAV instead of calling TTS — more reliable at the
# moment of failure and sounds more natural than a synthesised apology.
_FALLBACK_WAV_SENTINEL = {"use_fallback_wav": True, "intent": None}

# Scenario 4 fanout costs a full extra sarvam-30b round trip (~2-14s measured,
# reasoning tokens included) to sometimes save one clarifying question — a net
# latency loss on this model. Off by default; set INTENT_FANOUT_ENABLED=true
# to re-enable. The normal clarification loop (prompt Example 11) covers the
# ambiguous case either way.
INTENT_FANOUT_ENABLED = os.environ.get("INTENT_FANOUT_ENABLED", "false").lower() == "true"

_INTAKE_FIELDS = ("intent", "phone", "patient_name", "age", "department", "urgency", "chief_complaint")

_DIGIT_WORD_MAP = {
    # English
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    # Hindi / Urdu romanised
    "shunya": "0", "ek": "1", "do": "2", "teen": "3", "char": "4",
    "paanch": "5", "chheh": "6", "saat": "7", "aath": "8", "nau": "9",
    # Marathi romanised variants
    "don": "2", "tin": "3", "paach": "5", "saha": "6", "nav": "9",
    # Devanagari transliterations of English digit words
    # (Sarvam STT outputs these when a Hindi/Marathi speaker says English digit words)
    "नाईन": "9", "नाइन": "9",
    "एट": "8", "ऐट": "8",
    "सेव्हन": "7", "सेवन": "7",
    "सिक्स": "6", "सिक्‍स": "6",
    "फाईव्ह": "5", "फाइव": "5", "फाईव": "5",
    "फोर": "4", "फ़ोर": "4",
    "थ्री": "3", "थ्री": "3",
    "टू": "2", "टु": "2",
    "वन": "1", "वन्": "1",
    "झीरो": "0", "झिरो": "0",
}


def _digits_from_text(text: str) -> str:
    """Extract digit characters from text, converting word-form digits to numerals."""
    result = []
    for token in re.split(r"[\s,\-]+", text.lower()):
        if token in _DIGIT_WORD_MAP:
            result.append(_DIGIT_WORD_MAP[token])
        else:
            result.extend(c for c in token if c.isdigit())
    return "".join(result)


def _try_combine_partial_phone(collected: dict, messages: list[dict]) -> str | None:
    """If the LLM returned phone=None but the conversation history has digit sequences,
    attempt to combine prior partial digits + current message digits into a 10-digit number."""
    existing = (collected.get("phone") or "").replace(" ", "")
    if len(existing) == 10:
        return existing  # already complete

    # Scan all user messages for digit groups
    all_digits = ""
    for msg in messages:
        if msg["role"] == "user":
            all_digits += _digits_from_text(msg["content"])

    if len(all_digits) == 10:
        logger.info("voice_intake: assembled phone from conversation history: %s", all_digits)
        return all_digits
    if len(all_digits) > 10:
        # Take last 10 digits (most recent partial overrides older noise)
        candidate = all_digits[-10:]
        logger.info("voice_intake: trimmed phone digits to last 10: %s", candidate)
        return candidate
    return None


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

@traceable(run_type="llm", name="sarvam-30b:intake_extract_stream")
def _sync_stream_extract(full_messages: list[dict]) -> tuple[str, str | None]:
    """Run synchronous SDK streaming in a thread.

    Returns (accumulated_text, first_phone_found_mid_stream).
    Running in a thread (via asyncio.to_thread) keeps the event loop free
    for LiveKit audio heartbeats and data-channel messages during the ~1.9s
    LLM call.
    """
    import time as _time
    t_start = _time.perf_counter()
    # temperature=0: structured extraction. max_tokens is a runaway guard only
    # — sarvam-30b emits ~300-400 hidden reasoning tokens before content, so a
    # tight cap truncates the answer to nothing (verified empirically).
    stream = client.chat.completions(
        messages=full_messages, model="sarvam-30b", stream=True,
        temperature=0.0, max_tokens=2048,
    )
    accumulated = ""
    early_phone: str | None = None
    ttft_ms: float | None = None
    for chunk in stream:
        delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
        if delta and ttft_ms is None:
            ttft_ms = (_time.perf_counter() - t_start) * 1000
        accumulated += delta
        if early_phone is None and '"phone"' in accumulated:
            phone = _try_extract_phone(accumulated)
            if phone:
                early_phone = phone
    try:
        run = get_current_run_tree()
        if run is not None:
            run.metadata["ttft_ms"] = round(ttft_ms) if ttft_ms is not None else None
            run.metadata["model"] = "sarvam-30b"
    except Exception:
        pass  # tracing must never break the voice path
    return accumulated, early_phone


@traceable(run_type="llm", name="sarvam-30b:intake_extract_batch")
def _sync_batch_extract(full_messages: list[dict]) -> str:
    """Non-streaming fallback, also run in a thread."""
    r = client.chat.completions(
        messages=full_messages, model="sarvam-30b", temperature=0.0, max_tokens=2048
    )
    return r.choices[0].message.content or ""


async def _extract_patient_info(
    messages: list[dict],
    lang_code: str,
    already_collected: dict,
    session_id: str,
) -> tuple[dict, asyncio.Task | None]:
    """Call sarvam-30b to extract intake fields.

    Both streaming and fallback paths run inside asyncio.to_thread so the
    synchronous Sarvam SDK never blocks the LiveKit event loop.

    Returns:
        (parsed_dict, phone_lookup_task | None)
    """
    system_prompt = build_voice_intake_prompt(lang_code, already_collected)
    full_messages = [{"role": "system", "content": system_prompt}, *messages]
    phone_task: asyncio.Task | None = None

    try:
        # ── Scenario 3: streaming path (non-blocking via to_thread) ─────────
        try:
            accumulated, early_phone = await asyncio.wait_for(
                asyncio.to_thread(_sync_stream_extract, full_messages), timeout=LLM_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.error("voice_intake: LLM stream timed out after %.0fs — safe retry reply", LLM_TIMEOUT_S)
            return _FALLBACK_WAV_SENTINEL, None

        # Scenario 1: fire background DB lookup right after stream completes
        if early_phone and session_id not in _bg_tasks:
            logger.info("voice_intake: phone %s found post-stream — firing background lookup", early_phone)
            phone_task = asyncio.create_task(get_patient_record(early_phone))
            _bg_tasks[session_id] = phone_task

        parsed = extract_json(accumulated)
        if parsed is None:
            # Salvage the "reply" value out of malformed JSON before spending a
            # full extra sarvam-30b round trip (2-14s). Raw JSON must NEVER be
            # spoken to the patient (Scenario 1 field report).
            salvaged = extract_reply_text(accumulated)
            if salvaged:
                logger.warning("voice_intake: JSON malformed, salvaged reply text — skipping LLM retry")
                return {"reply": salvaged, "intent": None}, phone_task

            # Plain prose (no JSON syntax at all) → speak it directly.
            # This happens when sarvam-30b answers conversationally in Romanized
            # Hindi/Marathi without wrapping in JSON.  Retrying costs 20s and
            # ends in escalation; using the text directly is always better.
            if "{" not in accumulated and '"' not in accumulated:
                logger.info("voice_intake: plain-prose reply (no JSON syntax) — using directly")
                return {"reply": accumulated.strip(), "intent": None}, phone_task

            # sarvam-30b drops the JSON wrapper ~30-50% of the time on this
            # prompt (measured via evals, temperature-independent). One retry
            # with an explicit instruction recovers most of these — without it
            # the extracted intent is lost and the patient burns a whole
            # clarification round.
            logger.warning("voice_intake: reply not parseable JSON, retrying once: %r", accumulated[:200])
            retry_messages = [
                *full_messages,
                {"role": "assistant", "content": accumulated},
                {
                    "role": "user",
                    "content": (
                        "Your previous response was not a JSON object. Reply with ONLY "
                        "the single JSON object described in the system prompt — same "
                        "content, no prose outside the JSON."
                    ),
                },
            ]
            try:
                retry_reply = await asyncio.wait_for(
                    asyncio.to_thread(_sync_batch_extract, retry_messages), timeout=LLM_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                logger.error("voice_intake: JSON retry timed out — safe retry reply")
                return _FALLBACK_WAV_SENTINEL, phone_task
            parsed = extract_json(retry_reply)
            if parsed is None:
                logger.warning("voice_intake: retry still not JSON — safe retry reply (never speak raw JSON)")
                # Only speak the first reply if it contains no JSON syntax at all
                # (a plain conversational clarification); otherwise use the safe ask.
                if "{" not in accumulated and '"' not in accumulated:
                    return {"reply": accumulated, "intent": None}, phone_task
                return _FALLBACK_WAV_SENTINEL, phone_task
            logger.info("voice_intake: JSON retry succeeded")
        return parsed, phone_task

    except (AttributeError, TypeError, NotImplementedError):
        # ── Fallback: non-streaming, also non-blocking ────────────────────────
        logger.info("voice_intake: sarvam SDK does not support streaming — falling back to batch call")
        try:
            reply = await asyncio.wait_for(
                asyncio.to_thread(_sync_batch_extract, full_messages), timeout=LLM_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            logger.error("voice_intake: batch LLM call timed out — safe retry reply")
            return _FALLBACK_WAV_SENTINEL, None
        if not reply:
            logger.warning("voice_intake: API returned empty/None content")
            return {"reply": None, "intent": None}, None
        parsed = extract_json(reply)
        if parsed is None:
            logger.warning("voice_intake: batch reply not parseable JSON: %r", reply[:200])
            salvaged = extract_reply_text(reply)
            if salvaged:
                return {"reply": salvaged, "intent": None}, None
            if "{" not in reply and '"' not in reply:
                return {"reply": reply, "intent": None}, None
            return _FALLBACK_WAV_SENTINEL, None

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

    # Unrecoverable LLM failure — bubble the flag up to livekit_agent.py which
    # will play the pre-recorded fallback WAV and escalate without calling TTS.
    if extracted.get("use_fallback_wav"):
        logger.error("voice_intake: LLM unrecoverable — returning fallback WAV sentinel (session_id=%s)", session_id)
        return {
            **state,
            "use_fallback_wav": True,
            "escalation_required": True,
            "escalation_reason": "LLM unrecoverable after retries",
        }

    _ACTIONABLE_INTENTS = {"book", "prescription", "lab", "billing", "followup"}
    for field in _INTAKE_FIELDS:
        new_val = extracted.get(field)
        if new_val is None:
            continue
        if field == "intent" and new_val == "query":
            # Never let "query" overwrite an already-locked actionable intent.
            if collected.get("intent") in _ACTIONABLE_INTENTS:
                continue
            # Also skip "query" when the utterance only contained phone/name digits
            # (patient was just providing identity info, not expressing a new intent).
            last_user_msg = next(
                (m["content"] for m in reversed(state["messages"]) if m["role"] == "user"),
                "",
            )
            phone_only_pattern = re.fullmatch(r"[\d\s\-+()]+", last_user_msg.strip())
            if phone_only_pattern:
                continue
        collected[field] = new_val

    intent = collected.get("intent")
    reply_text = extracted.get("reply")

    # Guardrail: sarvam-30b defaults to Hindi regardless of lang_code because all
    # few-shot examples in the prompt are in Hindi. Translate the reply to the correct
    # language as a fallback — Sarvam Mayura (translate) is purpose-built for this.
    if reply_text and lang_code not in ("hi-IN", "en-IN"):
        try:
            reply_text = await translate_text(reply_text, "hi-IN", lang_code)
            logger.info("voice_intake: translated reply to %s", lang_code)
        except Exception:
            logger.exception("voice_intake: translation guardrail failed, using original reply")

    messages = [*state["messages"]]
    if reply_text:
        messages.append({"role": "assistant", "content": reply_text})

    # ── Scenario 4: confidence-gated fanout on first ambiguous turn ──────────
    attempt_count = state.get("intake_attempt_count", 0)
    if intent is None and attempt_count == 0 and INTENT_FANOUT_ENABLED and state["messages"]:
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

    # If LLM returned phone=None or a partial number (< 10 digits), attempt to
    # assemble a valid 10-digit number from the full conversation history.
    # This handles split-number patterns: patient says "987654" then "3210".
    phone_is_incomplete = not phone or (isinstance(phone, str) and len(re.sub(r"\D", "", phone)) < 10)
    if phone_is_incomplete and not patient_id:
        assembled = _try_combine_partial_phone(collected, state["messages"])
        if assembled:
            phone = assembled
            collected["phone"] = phone
            logger.info("voice_intake: partial phone assembled from history: %s", phone)
        elif phone and len(re.sub(r"\D", "", phone)) < 10:
            # LLM extracted a partial phone — clear it so phone gate triggers correctly
            logger.info("voice_intake: extracted phone %r has < 10 digits — treating as incomplete", phone)
            phone = None
            collected["phone"] = None

    # Phone gate: intent known but phone missing — prompt is already asking
    # (the LLM combined phone+symptom ask in its reply). Return intent so
    # Scenario 2 slot pre-fetch can fire immediately, but keep routing paused.
    if not phone and not patient_id:
        logger.info("voice_intake: intent=%s known, awaiting phone (LLM already asked)", intent)
        return {
            **state,
            "messages": messages,
            "intake_collected": collected,
            "intent": intent,          # ← promoted early for Scenario 2 slot pre-fetch
            "department": collected.get("department", state.get("department")),
            "urgency": collected.get("urgency", "normal"),
        }

    # Complaint gate: for booking, require chief_complaint before routing.
    # Without this, a patient who gives name then phone in separate turns gets
    # routed to the scheduler before ever stating their complaint.
    if intent == "book" and not collected.get("department") and not collected.get("chief_complaint"):
        if not collected.get("_complaint_turn"):
            # First time we have phone but no complaint — ask for it explicitly.
            collected["_complaint_turn"] = True
            complaint_ask_hi = "Aur aapko kya takleef ho rahi hai? Isse main aapko sahi doctor se milwa sakti hoon."
            try:
                complaint_reply = (
                    await translate_text(complaint_ask_hi, "hi-IN", lang_code)
                    if lang_code not in ("hi-IN", "en-IN")
                    else complaint_ask_hi
                )
            except Exception:
                complaint_reply = complaint_ask_hi
            # Replace any "booking" reply the LLM may have generated
            if messages and messages[-1].get("role") == "assistant":
                messages[-1]["content"] = complaint_reply
            else:
                messages.append({"role": "assistant", "content": complaint_reply})
            logger.info("voice_intake: intent=book, phone present but no complaint — asking for complaint")
            return {
                **state,
                "messages": messages,
                "intake_collected": collected,
                "intent": intent,
                "department": state.get("department"),
                "urgency": collected.get("urgency", "normal"),
            }
        else:
            # Patient didn't give complaint even after being asked — default to general
            collected["department"] = "general"
            logger.info("voice_intake: intent=book, complaint not given after ask — defaulting to general")
    elif intent == "book" and not collected.get("department"):
        # department can be inferred from chief_complaint but LLM didn't set it — default
        collected["department"] = "general"
        logger.info("voice_intake: intent=book, no department inferred — defaulting to general")

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

    # Hospital-wide availability fetch — fires once per booking call.
    # Runs in background; result awaited below before returning state.
    # Gives the scheduler LLM a full picture of all 15 departments so it
    # can answer "koi aur department hai?" without extra DB calls.
    availability_task = None
    if intent == "book" and not state.get("hospital_availability"):
        availability_task = asyncio.create_task(get_hospital_availability())
        logger.info("voice_intake: fired hospital_availability fetch")

    logger.info(
        "voice_intake: resolved intent=%s department=%s urgency=%s patient_id=%s",
        intent, department, collected.get("urgency", "normal"), patient_id,
    )

    # Await hospital availability if it was fetched this turn (usually already done
    # by the time patient_id resolution completes — overlap is the point).
    hospital_availability = state.get("hospital_availability")
    if availability_task is not None:
        try:
            hospital_availability = await asyncio.wait_for(availability_task, timeout=5.0)
            logger.info("voice_intake: hospital_availability fetched successfully")
        except Exception:
            logger.exception("voice_intake: hospital_availability fetch failed (non-fatal)")

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
        "hospital_availability": hospital_availability,
    }
