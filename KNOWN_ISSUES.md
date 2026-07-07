# Known Issues, Gaps & Optimization Opportunities

Running log from building + bottom-up testing the agents/api/analytics/voice
layers. Organized by severity. Update this as items get fixed or new ones
are found — don't let it go stale.

---

## Deferred bugs (found, not yet fixed)

- **Job disambiguation gap** — `get_due_outbound_jobs()` (agents/tools/db_tools.py)
  returns `{patient_id, lang_code, tts_voice, job_type}` but never the
  `discharge_followups.id`. If a patient ever has two pending jobs of the
  same `job_type` at once (stray manual reschedule, retry after
  "unreachable"), `log_outcome`/`get_discharge_info`/`get_pending_discharge`
  all grab "any pending row of this type" via `.scalars().first()` with no
  ordering — non-deterministic which row gets read/updated. Fix: thread the
  specific job id through `AgentState` (e.g. a `job_id` field) end to end.

- **No retry scheduling for "unreachable" outbound calls** — agents/CLAUDE.md
  says an unreachable follow-up call should retry in 4 hours.
  `followup_outbound_node` (agents/agent_followup.py) logs
  `status="unreachable"` but never calls `schedule_outbound_job` to actually
  requeue it. Right now an unreachable call is a dead end.

- **`rx_reminder` jobs are never created anywhere.** `post_call_node`
  (analytics/call_analytics.py) only schedules `job_type="confirmation"`
  (after a booking) and `job_type="followup"` (from a pending discharge).
  Nothing in the codebase ever calls `schedule_outbound_job(..., job_type="rx_reminder", ...)`
  — so `prescription_outbound_node`'s medication-reminder flow is fully
  built but structurally unreachable via the cron path. Needs a trigger
  (e.g. schedule one when a prescription is issued/refilled, or a
  day-before-refill_date check in the cron query).

- **No job locking / dedup on the outbound cron.** `run_outbound_jobs`
  (api/main.py) reads all due jobs and processes them, but doesn't mark a
  job "claimed" before processing. If a batch takes longer than expected
  (slow LLM calls) and the next 30-min tick fires before the previous batch
  finishes, or if `run_outbound_jobs` is somehow invoked concurrently, the
  same job could be processed twice.

- **No max-retry/backoff on outbound job failures.** Since 09/2026 fix,
  one job's exception no longer kills the batch (caught + logged in
  `api/main.py`), but a permanently-failing job (e.g. bad patient_id) will
  just get retried every 30 minutes forever with no cap or alerting.

---

## Untested / unverified (no live signal available)

- **`sarvam_batch_stt`/`sarvam_analyze_call`** (analytics/call_analytics.py)
  — implemented against the SDK's documented `create_job` → `upload_files`
  → `start` → `wait_until_complete` → `download_outputs` flow (verified via
  source introspection), but never run against a real recorded call audio
  file. The output JSON shape assumption (`transcript` /
  `diarized_transcript.entries[].{speaker_id,transcript}`) is inferred from
  the sync STT response type, not confirmed from an actual batch job output.
- **`voice/livekit_agent.py`** — `llm_node`'s logic was verified with a
  mocked `AgentSession`/`ChatContext`, but never run against a real LiveKit
  room + live audio call. Turn-detection timing, interruption handling, and
  the STT auto-detect language metadata path are unverified in practice.
- **Twilio SMS (`send_sms`) and Slack webhook (`send_slack_alert`)** — both
  are unwired since `TWILIO_*`/`SLACK_WEBHOOK_URL` are blank in `.env`.
  Code path is straightforward (httpx POST / twilio-python call) but has
  never actually fired.
- **`human_handoff_node`'s `escalate_to_doctor` call** — same as above,
  blocked on Slack/Twilio credentials.

---

## Known simplifications (working, but not the "real" design)

- **STT auto-detect metadata isn't wired into `AgentState`.** agents/CLAUDE.md's
  intended flow: Saaras v3 with `language="unknown"` returns detected
  language + confidence in its response metadata, and the LiveKit layer is
  supposed to read that and set `state["detected_language"]`/
  `state["detection_confidence"]` before invoking the graph. Currently
  `voice/livekit_agent.py` never populates those fields, so
  `language_router_node` always falls back to the text-based
  `sarvam_identify_language` API call on every single turn — functionally
  correct (verified against native-script Hindi text), but an extra network
  round-trip per turn that the STT metadata path would have avoided.
- **LLM JSON extraction is best-effort, not guaranteed.** `agents/tools/llm_json.py`
  handles the common failure mode (JSON wrapped in prose + markdown code
  fence) via regex, not real structured-output/function-calling. sarvam-30b
  has no JSON mode enforced at the API level in this implementation, so any
  reply shape the model produces that isn't caught by the fallback regex
  will silently degrade to a "clarify" loop rather than crash — safe, but
  the model's actual intent could occasionally get lost.
- **`normalize_department()` is a small hardcoded synonym map**
  (agents/tools/db_tools.py), not a robust classifier. The voice_intake
  prompt now constrains the model to the 5 canonical values directly, and
  this is just the safety net — but an unusual phrasing the model doesn't
  map correctly, and that isn't in `_DEPARTMENT_SYNONYMS`, will still fall
  through unnormalized and match zero slots.
- **No LLM temperature pinning for structured-decision calls**
  (voice_intake/scheduler/prescription/followup all call `chat.completions`
  at default temperature). Since these calls are meant to produce
  consistent structured JSON rather than creative text, pinning
  `temperature=0` (or low) would likely improve reliability of the
  extraction step specifically.
- **Prompt adherence to `{lang_code}` isn't 100%.** Observed once in testing:
  Agent 5's closing checklist line came back in English despite
  `lang_code="mr-IN"`. Not a code bug, just an LLM instruction-following
  gap — worth a stronger/repeated instruction or a post-hoc language check
  + re-translate if this recurs.

---

## Missing entirely

- **Frontend** (Next.js UI, admin dashboard) — explicitly deferred by the
  user, not started.
- **No automated test suite.** Everything validated in this session was ad
  hoc scripts run against the real Supabase/Upstash/Sarvam services, not
  checked-in pytest tests. There's no regression safety net for future
  changes.
- **No auth on any API route.** `/api/analytics/calls`, `/api/doctors`,
  booking, etc. are all open with no auth layer — fine for a take-home demo,
  a real gap before any production use.
- **No conversation-length/context-window handling.** `AgentState["messages"]`
  grows unbounded for the life of a call; a very long call could
  eventually hit the LLM's context window. No truncation/summarization
  logic exists.
- **No circuit breaker / backoff for Sarvam API outages.** If the Sarvam
  API is down or slow, every turn in `voice/livekit_agent.py`'s `llm_node`
  will hit the generic `except Exception` and repeat the same "something
  went wrong" message with no backoff — could spam a stuck caller.

---

## Not optimized (works, but could be better)

- **`/api/analytics/calls`** (api/routes/analytics.py) pulls all `CallLog`
  rows for the window into Python memory and aggregates with
  `Counter`/`mean`, rather than using SQL-side aggregation
  (`GROUP BY`/`AVG`). Fine at demo scale (tens/hundreds of calls), would
  need rework before it's fine at thousands+.
- **No DB connection pool tuning.** `create_async_engine` in
  `api/database.py` uses SQLAlchemy defaults — for concurrent calls at any
  real scale, pool size/overflow should be explicitly configured against
  Supabase's connection limits.
- **No indexes beyond what's in `api/models.py`.** Should be fine for demo
  volume; worth revisiting if `call_logs`/`appointments` grow large.

---

## Environment/dependency gotchas discovered (already fixed, noted for awareness)

- `sarvamai` has no `1.0.0+` release — pinned to `>=0.1.20` in requirements.txt.
- `livekit` (PyPI) is just the RTC client; `livekit-api` (token generation)
  and `livekit-agents` are separate packages, both now in requirements.txt.
- `livekit-agents` requires Python ≥3.10 (project now on 3.11 via Homebrew).
- Supabase's dashboard connection string is `postgresql://`, not
  `postgresql+asyncpg://` — `api/database.py` now auto-normalizes this.
- Nothing loaded `.env` — `load_dotenv()` added as the first import in
  `api/main.py`, `voice/livekit_agent.py`, `api/seed.py`.
