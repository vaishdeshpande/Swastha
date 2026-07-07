"""Post-call analytics subgraph. Runs after every inbound call completes —
this is a LangGraph node (`post_call`), not a separate graph.

Batch STT + diarization on the recording, sarvam-30b analysis of the
transcript, then persists to Redis (Layer 2) and Supabase (Layer 3), and
schedules any outbound follow-up jobs the call implies.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from sarvamai import SarvamAI

from agents.state import AgentState
from agents.tools.db_tools import get_pending_discharge, schedule_outbound_job
from agents.tools.llm_json import extract_json
from agents.tools.redis_tools import save_call_summary, save_lang_preference
from api.database import async_session
from api.models import CallLog

client = SarvamAI(api_subscription_key=os.environ["SARVAM_API_KEY"])

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


async def post_call_node(state: AgentState) -> AgentState:
    """Post-call analytics: Batch STT + diarization + sarvam-30b analysis."""
    state["current_agent"] = "post_call"

    # 1. Save call summary to Redis (Layer 2)
    summary = generate_call_summary(state["messages"])
    if state.get("patient_id"):
        await save_call_summary(state["patient_id"], summary)

    # 2. Save language preference to Redis (Layer 2)
    if state.get("patient_id"):
        await save_lang_preference(state["patient_id"], state["lang_code"])

    analysis = None

    # 3-5. Batch STT + diarization, sarvam-30b analysis, Supabase call_logs
    if state.get("call_recording_path"):
        transcript = await sarvam_batch_stt(
            audio_path=state["call_recording_path"],
            model="saaras:v3",
            with_diarization=True,
        )
        analysis = await sarvam_analyze_call(transcript=transcript, analysis_points=ANALYSIS_POINTS)

        await save_call_log(
            patient_id=state["patient_id"],
            call_id=state["call_id"],
            recording_path=state["call_recording_path"],
            analytics_json=analysis,
            duration=analysis.get("call_duration_sec"),
            outcome=state.get("call_outcome"),
        )

    patient_id = state.get("patient_id")

    # 6. If this was a booking, schedule outbound confirmation at +2h
    if patient_id and state.get("intent") == "book":
        await schedule_outbound_job(
            patient_id=patient_id,
            job_type="confirmation",
            due_at=datetime.utcnow() + timedelta(hours=2),
        )

    # 7. Check if patient has a recent discharge — schedule follow-up
    discharge = await get_pending_discharge(patient_id) if patient_id else None
    if discharge:
        await schedule_outbound_job(
            patient_id=patient_id,
            job_type="followup",
            due_at=datetime.fromisoformat(discharge["due_at"]),
        )

    return {**state, "call_outcome": analysis if analysis is not None else state.get("call_outcome")}
