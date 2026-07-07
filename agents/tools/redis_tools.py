"""Upstash Redis operations — Layer 2 (short-term, cross-call) memory.

Uses the async upstash-redis client so it can be awaited from LangGraph
agent nodes without blocking the event loop.
"""

from __future__ import annotations

import logging
import os

from upstash_redis.asyncio import Redis

logger = logging.getLogger(__name__)

redis = Redis(
    url=os.environ["UPSTASH_REDIS_REST_URL"],
    token=os.environ["UPSTASH_REDIS_REST_TOKEN"],
)

RECENT_CALLS_TTL = 7 * 24 * 3600      # 7 days
SESSION_TTL = 30 * 60                 # 30 min
LANG_PREF_TTL = 90 * 24 * 3600        # 90 days


async def redis_get(key: str) -> str | None:
    """Get a value from Upstash Redis."""
    value = await redis.get(key)
    logger.debug("redis: GET %s -> %s", key, "hit" if value else "miss")
    return value


async def redis_set(key: str, value: str, ttl_seconds: int) -> None:
    """Set a value with TTL in Upstash Redis."""
    await redis.set(key, value, ex=ttl_seconds)
    logger.debug("redis: SET %s (ttl=%ds)", key, ttl_seconds)


async def save_call_summary(patient_id: str, summary: str) -> None:
    """Append call summary to recent_calls list (max 5, TTL 7 days)."""
    key = f"recent_calls:{patient_id}"
    await redis.lpush(key, summary)
    await redis.ltrim(key, 0, 4)  # Keep last 5
    await redis.expire(key, RECENT_CALLS_TTL)
    logger.info("redis: saved call summary for patient_id=%s (key=%s)", patient_id, key)


async def save_session_state(call_id: str, state_json: str) -> None:
    """Save call state snapshot for crash recovery (TTL 30 min)."""
    await redis.set(f"session:{call_id}", state_json, ex=SESSION_TTL)
    logger.debug("redis: saved session snapshot for call_id=%s (ttl=%ds)", call_id, SESSION_TTL)


async def save_lang_preference(patient_id: str, lang_code: str) -> None:
    """Cache patient's language preference (TTL 90 days)."""
    await redis.set(f"lang_pref:{patient_id}", lang_code, ex=LANG_PREF_TTL)
    logger.info("redis: saved lang_pref=%s for patient_id=%s", lang_code, patient_id)


async def get_recent_calls(patient_id: str) -> list[str]:
    """Get last 5 call summaries for context."""
    calls = await redis.lrange(f"recent_calls:{patient_id}", 0, 4)
    logger.debug("redis: get_recent_calls patient_id=%s -> %d call(s)", patient_id, len(calls))
    return calls


SLOT_CACHE_TTL = 5 * 60  # 5 min — slots change rarely within a single call window


async def cache_slots(department: str, date: str, slots: list[dict]) -> None:
    """Cache available slots for a department/date (Scenario 2 — pre-fetch on intent detection)."""
    import json
    key = f"slot_cache:{department}:{date}"
    await redis.set(key, json.dumps(slots), ex=SLOT_CACHE_TTL)
    logger.info("redis: cached %d slot(s) for department=%s date=%s (ttl=%ds)", len(slots), department, date, SLOT_CACHE_TTL)


async def get_cached_slots(department: str, date: str) -> list[dict] | None:
    """Read pre-fetched slot cache. Returns None on miss so caller can fall back to Supabase."""
    import json
    key = f"slot_cache:{department}:{date}"
    raw = await redis.get(key)
    if raw is None:
        logger.debug("redis: slot_cache miss for department=%s date=%s", department, date)
        return None
    slots = json.loads(raw)
    logger.info("redis: slot_cache hit for department=%s date=%s -> %d slot(s)", department, date, len(slots))
    return slots
