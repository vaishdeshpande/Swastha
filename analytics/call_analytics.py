"""Post-call analytics subgraph. Runs after every inbound call completes —
this is a LangGraph node (`post_call`), not a separate graph.

Batch STT + diarization on the recording, sarvam-30b analysis of the
transcript, then persists to Redis (Layer 2) and Supabase (Layer 3), and
schedules any outbound follow-up jobs the call implies.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from langsmith import traceable
from sarvamai import SarvamAI

from agents.state import AgentState
from agents.tools.db_tools import get_pending_discharge, has_pending_job, schedule_outbound_job
from agents.tools.pii_tools import scrub_pii
from agents.tools.llm_json import extract_json
from agents.tools.redis_tools import save_call_summary, save_lang_preference
from api.database import async_session
from api.models import CallLog

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

logger = logging.getLogger(__name__)

# Keep strong references to in-flight background tasks so they aren't GC'd
# mid-run; a done-callback discards them and surfaces any exception.
_bg_post_call_tasks: set[asyncio.Task] = set()


def _bg_task_done(task: asyncio.Task) -> None:
    _bg_post_call_tasks.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("post_call: background work failed", exc_info=task.exception())

ANALYSIS_POINTS = [
    "sentiment_score",        # -1.0 to 1.0
    "issue_resolved",         # bool
    "agent_talk_time_pct",    # 0-100
    "patient_talk_time_pct",  # 0-100
    "call_duration_sec",
    "key_topics",             # list of strings
]


def generate_call_summary(messages: list[dict]) -> str:
    """One-line summary for Redis recent_calls context — just the last few
    patient utterances, so Agent 2 never makes the patient re-explain."""
    user_turns = [m["content"] for m in messages if m.get("role") == "user"]
    return " | ".join(user_turns[-3:]) if user_turns else "No conversation recorded"


async def sarvam_batch_stt(audio_path: str, model: str, with_diarization: bool) -> str:
    """Runs Sarvam Batch STT + diarization on the call recording.
    Returns the diarized transcript as plain text ("speaker_id: text" per
    turn), or the plain transcript if diarization wasn't requested.

    The SDK's batch job only exposes file-level success/failure via
    get_file_results() — the actual transcript text has to be pulled from
    the downloaded per-file output JSON (same shape as the sync STT
    response: {transcript, diarized_transcript: {entries: [...]}}).
    """
    job = client.speech_to_text_job.create_job(model=model, with_diarization=with_diarization)
    job.upload_files(file_paths=[audio_path])
    job.start()
    job.wait_until_complete()
    if job.is_failed():
        raise RuntimeError(f"Batch STT job failed for {audio_path}")

    with tempfile.TemporaryDirectory() as output_dir:
        job.download_outputs(output_dir)
        transcripts = []
        for output_file in Path(output_dir).glob("*.json"):
            result = json.loads(output_file.read_text())
            diarized = result.get("diarized_transcript")
            if with_diarization and diarized:
                transcripts.append(
                    "\n".join(f"{e['speaker_id']}: {e['transcript']}" for e in diarized["entries"])
                )
            else:
                transcripts.append(result.get("transcript", ""))
        return "\n".join(transcripts)


@traceable(run_type="llm", name="sarvam-30b:call_analysis")
async def sarvam_analyze_call(transcript: str, analysis_points: list[str]) -> dict:
    """Runs sarvam-30b over the diarized transcript to extract the
    requested analysis points as a JSON object."""
    prompt = (
        "Analyze this hospital receptionist call transcript. Return ONLY a "
        f"JSON object with these keys: {', '.join(analysis_points)}.\n\n"
        f"Transcript:\n{transcript}"
    )
    response = client.chat.completions(
        messages=[{"role": "user", "content": prompt}],
        model="sarvam-30b",
    )
    parsed = extract_json(response.choices[0].message.content)
    return parsed if parsed is not None else {point: None for point in analysis_points}


async def save_call_log(
    patient_id: str,
    call_id: str,
    recording_path: str,
    analytics_json: dict,
    duration: int | None,
    outcome: dict | None,
) -> None:
    async with async_session() as session:
        session.add(
            CallLog(
                patient_id=patient_id,
                call_id=call_id,
                recording_path=recording_path,
                analytics_json=analytics_json,
                duration_sec=duration,
                call_outcome=outcome,
                escalated=bool(outcome and outcome.get("status") == "escalated"),
            )
        )
        await session.commit()


async def _post_call_work(snapshot: AgentState) -> None:
    """The actual post-call persistence/analytics — runs as a background task
    so it never sits in the voice latency path. Operates on a snapshot of the
    turn's state; the live conversation state is not touched."""
    # ── Guardrail 6: PII scrub before any logging ─────────────────────────────
    # Scrub the snapshot only — the live in-call messages must stay intact so
    # later turns can still see phone digits, ages, etc.
    scrubbed_messages = [
        {**m, "content": scrub_pii(m["content"])} if m.get("content") else m
        for m in snapshot.get("messages", [])
    ]
    patient_id = snapshot.get("patient_id")

    # 1. Save call summary to Redis (Layer 2)
    if patient_id:
        await save_call_summary(patient_id, generate_call_summary(scrubbed_messages))

    # 2. Save language preference to Redis (Layer 2)
    if patient_id:
        await save_lang_preference(patient_id, snapshot["lang_code"])

    # 3-5. Batch STT + diarization, sarvam-30b analysis, Supabase call_logs
    if snapshot.get("call_recording_path"):
        transcript = await sarvam_batch_stt(
            audio_path=snapshot["call_recording_path"],
            model="saaras:v3",
            with_diarization=True,
        )
        analysis = await sarvam_analyze_call(transcript=transcript, analysis_points=ANALYSIS_POINTS)

        await save_call_log(
            patient_id=patient_id,
            call_id=snapshot["call_id"],
            recording_path=snapshot["call_recording_path"],
            analytics_json=analysis,
            duration=analysis.get("call_duration_sec"),
            outcome=snapshot.get("call_outcome"),
        )

    # 6. Schedule outbound confirmation at +2h — only once a booking actually
    #    completed this call (appointment_id set), and only if one isn't
    #    already pending. post_call runs on every specialist turn, so without
    #    both gates this used to create one duplicate job per turn.
    if patient_id and snapshot.get("appointment_id"):
        if not await has_pending_job(patient_id, "confirmation"):
            await schedule_outbound_job(
                patient_id=patient_id,
                job_type="confirmation",
                due_at=datetime.utcnow() + timedelta(hours=2),
            )

    # 7. A pending discharge follow-up row IS the scheduled job — the cron
    #    picks it up via get_due_outbound_jobs. Re-inserting a copy here (the
    #    old behavior) duplicated the job on every turn; just log it instead.
    discharge = await get_pending_discharge(patient_id) if patient_id else None
    if discharge:
        logger.debug(
            "post_call: patient %s has pending discharge follow-up due_at=%s (cron will pick it up)",
            patient_id, discharge["due_at"],
        )


async def post_call_node(state: AgentState) -> AgentState:
    """Fires the post-call persistence/analytics as a background task and
    returns immediately — this node used to add ~2s of Redis/DB writes to
    every specialist turn's voice latency."""
    state["current_agent"] = "post_call"

    # Shallow-copy the state and message list so background work reads a
    # stable snapshot even if the next turn mutates the live state.
    snapshot: AgentState = {**state, "messages": [dict(m) for m in state.get("messages", [])]}
    task = asyncio.create_task(_post_call_work(snapshot))
    _bg_post_call_tasks.add(task)
    task.add_done_callback(_bg_task_done)

    return state
