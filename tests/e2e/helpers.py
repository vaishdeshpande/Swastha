"""Shared helpers for e2e tests.

`run_turn()` mirrors exactly what livekit_agent.llm_node() does each time
the patient speaks: append user message → ainvoke graph → return last
assistant reply.

`print_state()` dumps the key fields so you can see what each agent wrote
when running with pytest -s.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def fresh_state(call_id: str = "e2e-call-001", **overrides) -> dict:
    """Build the initial AgentState exactly as livekit_agent._initial_state() does."""
    state = {
        "lang_code": "hi-IN",
        "tts_voice": "priya",
        "tts_model": "bulbul:v3",
        "detected_language": None,
        "detection_confidence": None,
        "patient_id": None,
        "patient_name": None,
        "is_new_patient": False,
        "intent": None,
        "department": None,
        "urgency": "normal",
        "intake_attempt_count": 0,
        "messages": [],
        "current_agent": "language_router",
        "escalation_required": False,
        "escalation_reason": None,
        "call_id": call_id,
        "call_recording_path": None,
        "call_outcome": None,
        "call_start_time": datetime.now(timezone.utc).isoformat(),
        "offered_slots": None,
        "appointment_id": None,
        "job_type": None,
        "call_connected": True,
    }
    state.update(overrides)
    return state


async def run_turn(graph, state: dict, user_text: str) -> tuple[str, dict]:
    """Simulate one patient utterance through the full graph.

    Appends user_text to state["messages"] (what livekit_agent does), then
    calls graph.ainvoke(). Returns (assistant_reply, updated_state).
    """
    state["messages"].append({"role": "user", "content": user_text})
    state = await graph.ainvoke(state)
    reply = state["messages"][-1]["content"] if state["messages"] else ""
    return reply, state


def print_state(label: str, state: dict) -> None:
    """Print a readable state snapshot. Use with pytest -s."""
    divider = "─" * 60
    print(f"\n{divider}")
    print(f"  {label}")
    print(divider)
    print(f"  current_agent   : {state.get('current_agent')}")
    print(f"  lang_code       : {state.get('lang_code')} / voice={state.get('tts_voice')}")
    print(f"  patient_id      : {state.get('patient_id')} (new={state.get('is_new_patient')})")
    print(f"  intent          : {state.get('intent')}  department={state.get('department')}")
    print(f"  urgency         : {state.get('urgency')}")
    print(f"  appointment_id  : {state.get('appointment_id')}")
    print(f"  escalation      : {state.get('escalation_required')} — {state.get('escalation_reason')}")
    print(f"  call_outcome    : {state.get('call_outcome')}")
    print(f"  messages ({len(state['messages'])} total):")
    for m in state["messages"]:
        role = m["role"].upper()
        content = m["content"]
        if len(content) > 120:
            content = content[:117] + "..."
        print(f"    [{role}] {content}")
    print(divider)
