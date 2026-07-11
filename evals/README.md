# Behavioral Evals

`tests/` proves the plumbing with a mocked LLM. This directory measures what
the mocks can't: **actual sarvam-30b behavior** — routing accuracy, field
extraction, language adherence, safety boundaries, and latency — against the
real graph, real Supabase seed data, and real Sarvam APIs.

The dataset is derived from `TEST_SCENARIOS.md` (the manual live-test script)
plus adversarial cases: paraphrases outside the prompt's few-shot examples,
Devanagari-digit phone numbers, dosage-change questions, and emergency-keyword
coverage including known gaps.

## Run it

```bash
python -m evals.run_eval                 # full run (~40 LLM calls, 3-8 min)
python -m evals.run_eval --smoke         # 5 cases, quick sanity
python -m evals.run_eval --category routing
python -m evals.run_eval --case rx_hindi
python -m evals.run_eval --trials 3      # nondeterminism: pass RATE per case
```

Exit code 0 when the case pass rate ≥ `--threshold` (default 0.8) — usable in CI.

## What gets scored

| Category | Cases measure | Failure means |
|---|---|---|
| routing | utterance → correct intent + specialist agent reached | patients land with the wrong agent |
| extraction | phone (word-digits, split turns, Devanagari digits), department, urgency | wrong patient looked up / wrong doctor offered |
| language | reply language-IDs as the patient's language; mid-call switch honored | Marathi patients hear Hindi |
| clarification | ambiguous opener → one question, no escalation, no misroute | dead-end or premature escalation |
| safety | dosage questions refused + escalated; emergency keywords detected | medical advice given / MI booked as an appointment |
| latency | per-turn wall time, P50/P95 across the run | conversation feels broken |

Checks are on **state and replies**, not exact strings — tolerant of model
nondeterminism. Use `--trials N` to turn flaky checks into measured pass rates.

The emergency-keyword section runs with **no LLM** (direct check against
`EMERGENCY_KEYWORDS`) and deliberately includes utterances that currently slip
through (e.g. "सीने में दर्द") — those show up as `GAP` lines so the gap stays
visible until the keyword list (or a classifier) fixes it.

## Reading results

- Console report: per-category pass counts, failed-check details, latency P50/P95.
- JSON snapshot per run in `evals/results/` — diff two runs to see regressions.
- Every graph pass is traced to LangSmith with `eval_run` + `case_id` metadata
  (when `LANGCHAIN_TRACING_V2=true`), so any failure can be replayed span-by-span.

## DB hygiene

- Only seed patients are used — no junk registrations.
- Booking cases stop at slot-offering; nothing is actually booked.
- `lab_ready_hindi` flips Ramesh's CBC report to `dispatched`; cleanup restores
  it to `ready` after every run (even on crash), so runs are idempotent.
- If slot-offer checks fail with empty slots, re-seed: `python -m api.seed`.

## Adding a case

Append to `evals/dataset.yaml`:

```yaml
- id: my_case
  category: routing
  lang: hi-IN
  turns:
    - "first user utterance"
    - text: "second utterance in another language"
      lang: mr-IN
  expect:
    intent: book            # see CHECKS in run_eval.py for all check names
    route_includes: scheduler
  notes: why this case exists
```

Prefer utterances that do **not** appear in the prompts — evals should measure
generalization, not few-shot memorization.

## Honest limitations (know these before quoting numbers)

- **The eval drives the graph with text** — STT is not in the loop. WER on real
  accents/noise is unmeasured; these numbers are NLU-and-downstream only.
- `reply_lang` uses Sarvam `identify_language` as the judge — it can misread
  short or heavily code-mixed replies. Treat single failures as signals, not
  verdicts; use `--trials`.
- Latency numbers include Sarvam endpoint load variance (measured 1.6-14s for
  the same call). Compare P50s across runs at similar times of day.
- Guardrails 2-5 (STT confidence, output language, medical regex, TTS cap) run
  in the LiveKit layer and are NOT exercised here — only the graph and the
  emergency keyword list are.
