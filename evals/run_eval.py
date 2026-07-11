"""Behavioral eval runner — drives the REAL inbound graph (real sarvam-30b,
real Supabase/Redis) against evals/dataset.yaml and scores the outcomes.

This is NOT the pytest suite. tests/ mocks the LLM and proves plumbing;
this measures model behavior: routing accuracy, extraction, language
adherence, safety, and latency. Results are rates, not assertions — rerun
with --trials N to measure nondeterminism.

Usage:
    python -m evals.run_eval                     # full run (~40 LLM calls)
    python -m evals.run_eval --category routing  # one category
    python -m evals.run_eval --case rx_hindi     # one case
    python -m evals.run_eval --trials 3          # repeat each case 3x
    python -m evals.run_eval --smoke             # 5-case quick check

Cost/time: each turn is 1-2 sarvam-30b calls (2-14s each depending on
endpoint load). Full run ≈ 3-8 minutes. Traces land in LangSmith tagged
with eval_run + case_id metadata when LANGCHAIN_TRACING_V2=true.

DB hygiene: uses seed patients only (no junk registration); booking cases
stop at slot-offering (nothing is booked); the lab_ready case flips Ramesh's
CBC report to 'dispatched' and cleanup() restores it to 'ready' after the run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

import yaml

RESULTS_DIR = Path(__file__).resolve().parent / "results"

SMOKE_CASES = [
    "rx_hindi", "book_hindi_full_info", "billing_hindi",
    "ambiguous_first_utterance", "medical_advice_boundary",
]


# ---------------------------------------------------------------------------
# State factory — mirrors what voice/livekit_agent.py builds per call
# ---------------------------------------------------------------------------

def fresh_state(lang: str) -> dict:
    return {
        "session_id": f"eval-{uuid.uuid4().hex[:12]}",
        "lang_code": lang,
        "tts_voice": "priya" if lang != "mr-IN" else "kavya",
        "tts_model": "bulbul:v3",
        "detected_language": lang,
        "detection_confidence": 0.95,
        "lang_mismatch_count": 0,
        "patient_id": None, "patient_name": None, "is_new_patient": False,
        "intent": None, "department": None, "urgency": "normal",
        "intake_attempt_count": 0, "intake_collected": {},
        "messages": [], "current_agent": "",
        "escalation_required": False, "escalation_reason": None,
        "call_id": f"eval-{uuid.uuid4().hex[:12]}",
        "call_recording_path": None, "call_outcome": None,
        "call_start_time": datetime.now(timezone.utc).isoformat(),
        "offered_slots": None, "appointment_id": None, "booked_slot_details": None,
        "job_type": None, "call_connected": True,
        "optimistic_patient_id": None, "prefetched_slots": None,
        "intent_classifier_scores": None, "lab_reports_dispatched": None,
        "bill_amount_due": None, "bill_sms_sent": None,
    }


# ---------------------------------------------------------------------------
# Checks — each takes (state, last_reply, expected_value) -> (ok, detail)
# ---------------------------------------------------------------------------

def _devanagari_fraction(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)


def check_intent(state, reply, expected):
    got = state.get("intent")
    return got == expected, f"intent={got!r}"


def check_department(state, reply, expected):
    got = state.get("department")
    return got == expected, f"department={got!r}"


def check_urgency(state, reply, expected):
    got = state.get("urgency")
    return got == expected, f"urgency={got!r}"


def check_route_includes(state, reply, expected):
    path = state.get("_eval_path", [])
    return expected in path, f"path={path}"


def check_escalation(state, reply, expected):
    got = bool(state.get("escalation_required"))
    return got == bool(expected), f"escalation_required={got}"


def check_offered_slots(state, reply, expected):
    got = bool(state.get("offered_slots"))
    return got == bool(expected), f"offered_slots={'yes' if got else 'no'}"


def check_bill_amount(state, reply, expected):
    got = state.get("bill_amount_due")
    if expected is None:
        return got is None, f"bill_amount_due={got!r}"
    return got is not None and abs(float(got) - float(expected)) < 0.01, f"bill_amount_due={got!r}"


def check_lab_dispatched(state, reply, expected):
    got = bool(state.get("lab_reports_dispatched"))
    return got == bool(expected), f"lab_reports_dispatched={state.get('lab_reports_dispatched')!r}"


def check_phone_collected(state, reply, expected):
    import re
    got = (state.get("intake_collected") or {}).get("phone") or ""
    digits = re.sub(r"\D", "", str(got))[-10:]
    return digits == expected, f"phone_collected={got!r}"


def check_lang_code(state, reply, expected):
    got = state.get("lang_code")
    return got == expected, f"lang_code={got!r}"


def check_reply_nonempty(state, reply, expected):
    ok = bool(reply and reply.strip())
    return ok == bool(expected), f"reply={'<empty>' if not reply else reply[:60]!r}"


def check_reply_script(state, reply, expected):
    frac = _devanagari_fraction(reply or "")
    if expected == "devanagari":
        return frac >= 0.3, f"devanagari_fraction={frac:.2f}"
    return frac < 0.3, f"devanagari_fraction={frac:.2f}"


def check_reply_not_contains(state, reply, expected):
    lower = (reply or "").lower()
    hits = [t for t in expected if t.lower() in lower]
    return not hits, f"forbidden terms found: {hits}" if hits else "clean"


async def check_reply_lang(state, reply, expected):
    """LLM-judge-lite: Sarvam identify_language on the reply (~200ms)."""
    if not reply:
        return False, "reply empty"
    from agents.tools.translate_tools import sarvam_identify_language
    detected = await sarvam_identify_language(reply)
    return detected == expected, f"identify_language={detected!r}"


CHECKS = {
    "intent": check_intent,
    "department": check_department,
    "urgency": check_urgency,
    "route_includes": check_route_includes,
    "escalation": check_escalation,
    "offered_slots": check_offered_slots,
    "bill_amount": check_bill_amount,
    "lab_dispatched": check_lab_dispatched,
    "phone_collected": check_phone_collected,
    "lang_code": check_lang_code,
    "reply_nonempty": check_reply_nonempty,
    "reply_script": check_reply_script,
    "reply_not_contains": check_reply_not_contains,
    "reply_lang": check_reply_lang,   # async — awaited by the scorer
}


# ---------------------------------------------------------------------------
# Case execution
# ---------------------------------------------------------------------------

async def run_case(graph, case: dict, run_id: str) -> dict:
    state = fresh_state(case.get("lang", "hi-IN"))
    path: list[str] = []
    turn_latencies: list[float] = []
    last_reply = ""

    for turn in case["turns"]:
        if isinstance(turn, dict):
            text, turn_lang = turn["text"], turn.get("lang")
        else:
            text, turn_lang = turn, None
        if turn_lang:
            state["detected_language"] = turn_lang
            state["detection_confidence"] = 0.95

        state["messages"] = [*state["messages"], {"role": "user", "content": text}]
        t0 = time.perf_counter()
        async for update in graph.astream(
            state,
            stream_mode="updates",
            config={"metadata": {"eval_run": run_id, "case_id": case["id"]}},
        ):
            for node_name, node_state in update.items():
                path.append(node_name)
                if node_state is not None:
                    state = {**state, **node_state}
        turn_latencies.append(time.perf_counter() - t0)
        last_reply = next(
            (m["content"] for m in reversed(state["messages"]) if m["role"] == "assistant"), ""
        )

    state["_eval_path"] = path

    # Score
    results = {}
    for name, expected in (case.get("expect") or {}).items():
        fn = CHECKS.get(name)
        if fn is None:
            results[name] = {"ok": False, "detail": f"unknown check {name!r}"}
            continue
        try:
            out = fn(state, last_reply, expected)
            ok, detail = (await out) if asyncio.iscoroutine(out) else out
        except Exception as exc:  # a check must never kill the run
            ok, detail = False, f"check raised: {exc}"
        results[name] = {"ok": bool(ok), "detail": detail}

    return {
        "id": case["id"],
        "category": case.get("category", "uncategorized"),
        "passed": all(r["ok"] for r in results.values()),
        "checks": results,
        "turn_latencies_ms": [round(t * 1000) for t in turn_latencies],
        "reply": (last_reply or "")[:160],
    }


# ---------------------------------------------------------------------------
# Emergency keyword coverage — no LLM, direct guardrail check
# ---------------------------------------------------------------------------

def run_emergency_coverage(utterances: list[dict]) -> list[dict]:
    """Runs the PRODUCTION emergency detector (voice.livekit_agent.detect_emergency)
    over the coverage utterances — no LLM involved."""
    from voice.livekit_agent import detect_emergency

    out = []
    for u in utterances:
        matched = detect_emergency(u["text"], u["lang"])
        detected = matched is not None
        out.append({
            "text": u["text"], "lang": u["lang"],
            "should_detect": u["should_detect"], "detected": detected,
            "matched_keyword": matched,
            "passed": detected == u["should_detect"],
        })
    return out


# ---------------------------------------------------------------------------
# Cleanup — restore DB state mutated by eval cases
# ---------------------------------------------------------------------------

async def cleanup():
    """Reset the lab_ready_hindi mutation (CBC flipped to 'dispatched') so the
    eval is idempotent across runs."""
    from sqlalchemy import select, update
    from api.database import async_session
    from api.models import LabReport, Patient

    async with async_session() as s:
        ramesh = (await s.execute(
            select(Patient.id).where(Patient.phone == "+919876543210")
        )).scalar_one_or_none()
        if ramesh:
            await s.execute(
                update(LabReport)
                .where(
                    LabReport.patient_id == ramesh,
                    LabReport.test_name.ilike("%CBC%"),
                    LabReport.status == "dispatched",
                )
                .values(status="ready")
            )
            await s.commit()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(case_results: list[dict], emergency_results: list[dict], trials: int):
    print("\n" + "═" * 78)
    print("EVAL REPORT")
    print("═" * 78)

    by_category: dict[str, list[dict]] = {}
    for r in case_results:
        by_category.setdefault(r["category"], []).append(r)

    for cat, rows in sorted(by_category.items()):
        # aggregate per case id across trials
        by_id: dict[str, list[dict]] = {}
        for r in rows:
            by_id.setdefault(r["id"], []).append(r)
        passed_cases = sum(1 for runs in by_id.values() if all(x["passed"] for x in runs))
        print(f"\n── {cat.upper()}  ({passed_cases}/{len(by_id)} cases pass)")
        for cid, runs in by_id.items():
            rate = sum(1 for x in runs if x["passed"]) / len(runs)
            flag = "✓" if rate == 1.0 else ("~" if rate > 0 else "✗")
            trial_note = f"  [{int(rate * len(runs))}/{len(runs)} trials]" if trials > 1 else ""
            print(f"  {flag} {cid}{trial_note}")
            worst = next((x for x in runs if not x["passed"]), None)
            if worst:
                for name, res in worst["checks"].items():
                    if not res["ok"]:
                        print(f"      ✗ {name}: {res['detail']}")

    if emergency_results:
        ok = sum(1 for r in emergency_results if r["passed"])
        print(f"\n── SAFETY: EMERGENCY KEYWORD COVERAGE  ({ok}/{len(emergency_results)} pass)")
        for r in emergency_results:
            if not r["passed"]:
                want = "detect" if r["should_detect"] else "NOT detect"
                print(f"  ✗ GAP: should {want}: {r['text']!r} ({r['lang']})")

    lats = [t for r in case_results for t in r["turn_latencies_ms"]]
    if lats:
        lats.sort()
        p50 = statistics.median(lats)
        p95 = lats[max(0, int(len(lats) * 0.95) - 1)]
        print(f"\n── LATENCY (per graph turn, before TTS)")
        print(f"  turns={len(lats)}  min={lats[0]:,} ms  P50={p50:,.0f} ms  P95={p95:,} ms  max={lats[-1]:,} ms")

    total_ids = {r["id"] for r in case_results}
    passed_ids = {r["id"] for r in case_results} - {
        r["id"] for r in case_results if not r["passed"]
    }
    emergency_pass = all(r["passed"] for r in emergency_results) if emergency_results else True
    print("\n" + "═" * 78)
    print(f"CASES: {len(passed_ids)}/{len(total_ids)} pass"
          f"   EMERGENCY COVERAGE: {'pass' if emergency_pass else 'GAPS FOUND'}")
    print("═" * 78)
    return len(passed_ids), len(total_ids)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    ap = argparse.ArgumentParser(description="Behavioral eval runner (real LLM calls)")
    ap.add_argument("--category", help="run only this category")
    ap.add_argument("--case", help="run only this case id")
    ap.add_argument("--trials", type=int, default=1, help="repeat each case N times")
    ap.add_argument("--smoke", action="store_true", help="quick 5-case run")
    ap.add_argument("--skip-emergency", action="store_true", help="skip keyword coverage checks")
    ap.add_argument("--threshold", type=float, default=0.8,
                    help="min case pass rate for exit code 0 (default 0.8)")
    args = ap.parse_args()

    dataset = yaml.safe_load((Path(__file__).resolve().parent / "dataset.yaml").read_text())
    cases = dataset["cases"]
    if args.smoke:
        cases = [c for c in cases if c["id"] in SMOKE_CASES]
    if args.category:
        cases = [c for c in cases if c.get("category") == args.category]
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    if not cases:
        print("no cases matched the filter")
        return 2

    from agents.graph import build_inbound_graph
    graph = build_inbound_graph()

    run_id = f"eval-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    print(f"run_id={run_id}  cases={len(cases)}  trials={args.trials}")
    print("(real sarvam-30b calls — expect 2-14s per turn depending on endpoint load)")

    case_results: list[dict] = []
    try:
        for case in cases:
            for trial in range(args.trials):
                t0 = time.perf_counter()
                try:
                    result = await run_case(graph, case, run_id)
                except Exception as exc:
                    result = {
                        "id": case["id"], "category": case.get("category", "?"),
                        "passed": False,
                        "checks": {"_run": {"ok": False, "detail": f"case raised: {exc}"}},
                        "turn_latencies_ms": [], "reply": "",
                    }
                status = "PASS" if result["passed"] else "FAIL"
                print(f"  [{status}] {case['id']}"
                      + (f" (trial {trial + 1})" if args.trials > 1 else "")
                      + f"  {time.perf_counter() - t0:.1f}s")
                case_results.append(result)
    finally:
        try:
            await cleanup()
        except Exception as exc:
            print(f"cleanup failed (non-fatal): {exc}")

    emergency_results = []
    if not args.skip_emergency and not args.case and not args.category:
        emergency_results = run_emergency_coverage(dataset.get("emergency_utterances", []))

    passed, total = print_report(case_results, emergency_results, args.trials)

    RESULTS_DIR.mkdir(exist_ok=True)
    out_path = RESULTS_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps({
        "run_id": run_id, "trials": args.trials,
        "cases": case_results, "emergency": emergency_results,
    }, ensure_ascii=False, indent=2))
    print(f"\nresults saved: {out_path}")
    print(f"LangSmith: filter traces by metadata eval_run={run_id!r}")

    return 0 if (total and passed / total >= args.threshold) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
