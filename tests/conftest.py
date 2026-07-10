"""Shared pytest fixtures and helpers.

All agent tests run without real Sarvam API credentials or a real DB.
External dependencies are patched at the boundary (SarvamAI client,
db_tools functions, redis_tools functions). This keeps tests fast,
deterministic, and runnable in CI without any env vars.
"""

from __future__ import annotations

import os
import pytest

# ---------------------------------------------------------------------------
# Stub out env vars that are read at module import time, before any
# agents/* modules are imported.
# ---------------------------------------------------------------------------
os.environ.setdefault("SARVAM_API_KEY", "test-key")
os.environ.setdefault("UPSTASH_REDIS_REST_URL", "https://test.upstash.io")
os.environ.setdefault("UPSTASH_REDIS_REST_TOKEN", "test-token")
os.environ.setdefault("LIVEKIT_API_KEY", "test-lk-key")
os.environ.setdefault("LIVEKIT_API_SECRET", "test-lk-secret")
os.environ.setdefault("SLACK_WEBHOOK_URL", "https://hooks.slack.com/test")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtest")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-auth")
os.environ.setdefault("TWILIO_PHONE_NUMBER", "+10000000000")
os.environ.setdefault("ON_CALL_DOCTOR_PHONE", "+10000000001")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")


# ---------------------------------------------------------------------------
# Base AgentState factory — tests override only the fields they care about.
# ---------------------------------------------------------------------------

def make_state(**overrides) -> dict:
    base = {
        "session_id": "test-session-001",
        "lang_code": "hi-IN",
        "tts_voice": "priya",
        "tts_model": "bulbul:v3",
        "detected_language": None,
        "detection_confidence": None,
        "lang_mismatch_count": 0,
        "patient_id": "patient-uuid-001",
        "patient_name": "Ramesh Kumar",
        "is_new_patient": False,
        "intent": None,
        "department": "general",
        "urgency": "normal",
        "intake_attempt_count": 0,
        "messages": [{"role": "user", "content": "Namaste, mujhe appointment chahiye"}],
        "current_agent": "language_router",
        "escalation_required": False,
        "escalation_reason": None,
        "call_id": "test-call-001",
        "call_recording_path": None,
        "call_outcome": None,
        "call_start_time": "2026-07-06T10:00:00+00:00",
        "offered_slots": None,
        "appointment_id": None,
        "job_type": None,
        "call_connected": True,
        "optimistic_patient_id": None,
        "prefetched_slots": None,
        "intent_classifier_scores": None,
        "lab_reports_dispatched": None,
        "bill_amount_due": None,
        "bill_sms_sent": None,
    }
    base.update(overrides)
    return base
