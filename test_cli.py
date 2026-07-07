"""Terminal CLI for testing the inbound LangGraph agent pipeline without voice.

Usage:
    python test_cli.py
    python test_cli.py --lang hi-IN
    python test_cli.py --lang mr-IN
    python test_cli.py --lang en-IN

Type your message and press Enter. Type 'exit' or Ctrl+C to quit.
Type '/state' to dump the current AgentState.
Type '/reset' to start a fresh conversation.
"""

import asyncio
import argparse
import json
import sys
import uuid
from datetime import datetime

# Load .env before importing anything that needs env vars
from dotenv import load_dotenv
load_dotenv()

from agents.graph import build_inbound_graph
from agents.state import AgentState

# ── ANSI colours for readability ─────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
GREY   = "\033[90m"
RED    = "\033[91m"

def clr(text: str, colour: str) -> str:
    return f"{colour}{text}{RESET}"


# ── Initial state factory ─────────────────────────────────────────────────────

def fresh_state(lang_hint: str) -> AgentState:
    """Returns a clean AgentState for a new conversation."""
    return AgentState(
        # Stable session identity — survives LiveKit reconnects and /reset.
        session_id=str(uuid.uuid4()),

        # Agent 1 will fill these properly; provide sensible defaults so
        # the graph doesn't crash before Agent 1 runs.
        lang_code=lang_hint,
        tts_voice="priya",
        tts_model="bulbul:v3",
        detected_language=lang_hint,
        detection_confidence=0.9,

        # Agent 2 fields — all empty at start
        patient_id=None,
        patient_name=None,
        is_new_patient=False,
        intent=None,
        department=None,
        urgency="normal",
        intake_attempt_count=0,
        intake_collected={},

        # Conversation
        messages=[],
        current_agent="",

        # Escalation
        escalation_required=False,
        escalation_reason=None,

        # Post-call
        call_id=str(uuid.uuid4()),
        call_recording_path=None,
        call_outcome=None,
        call_start_time=datetime.utcnow().isoformat(),

        # Scheduler
        offered_slots=None,
        appointment_id=None,

        # Outbound (unused for inbound CLI)
        job_type=None,
        call_connected=None,
    )


# ── Pretty printer ────────────────────────────────────────────────────────────

def print_agent_banner(agent_name: str) -> None:
    print(clr(f"\n  ▶ Agent active: {agent_name}", YELLOW))


def print_assistant_reply(text: str) -> None:
    print(clr(f"\n  Agent: {text}\n", GREEN))


def print_state_dump(state: AgentState) -> None:
    safe = {k: v for k, v in state.items() if k != "messages"}
    print(clr("\n── AgentState (excluding messages) ──", GREY))
    print(clr(json.dumps(safe, indent=2, default=str), GREY))
    print(clr(f"── {len(state['messages'])} messages in history ──\n", GREY))


def print_error(msg: str) -> None:
    print(clr(f"\n  ERROR: {msg}\n", RED))


# ── Main loop ─────────────────────────────────────────────────────────────────

async def run(lang_hint: str) -> None:
    graph = build_inbound_graph()
    state = fresh_state(lang_hint)

    print(clr("\n╔══════════════════════════════════════════════╗", CYAN))
    print(clr("║   Hospital Receptionist — Terminal Test CLI  ║", CYAN))
    print(clr("╚══════════════════════════════════════════════╝", CYAN))
    print(clr(f"  Language hint: {lang_hint}", GREY))
    print(clr("  Commands: /state · /reset · exit\n", GREY))

    while True:
        # Read user input
        try:
            raw = input(clr("You: ", BOLD)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not raw:
            continue

        # Built-in commands
        if raw.lower() == "exit":
            print("Bye!")
            break

        if raw == "/state":
            print_state_dump(state)
            continue

        if raw == "/reset":
            state = fresh_state(lang_hint)
            print(clr("  Conversation reset.\n", YELLOW))
            continue

        # Append user message and run one full graph pass
        state["messages"].append({"role": "user", "content": raw})

        print(clr("  ...", GREY), end="\r")
        try:
            result = await graph.ainvoke(
                state,
                config={"metadata": {
                    "session_id": state["session_id"],
                    "call_id": state["call_id"],
                }},
            )
        except Exception as exc:
            print_error(str(exc))
            # Pop the message we just appended so the user can retry
            state["messages"].pop()
            continue

        # Show which agent handled this turn
        active_agent = result.get("current_agent", "")
        if active_agent:
            print_agent_banner(active_agent)

        # Show the latest assistant reply (last message with role="assistant")
        assistant_msgs = [m for m in result["messages"] if m["role"] == "assistant"]
        if assistant_msgs:
            print_assistant_reply(assistant_msgs[-1]["content"])
        else:
            print(clr("  (no assistant reply in this turn)", GREY))

        # Persist state for the next turn — graph returns a new dict each pass
        state = result  # type: ignore[assignment]

        # If the graph reached a terminal node (escalation or post-call), reset
        outcome = result.get("call_outcome") or {}
        if result.get("escalation_required") or outcome.get("status") in ("escalated", "completed"):
            print(clr("\n  ── Conversation ended. Starting fresh. ──\n", YELLOW))
            state = fresh_state(lang_hint)


def main() -> None:
    parser = argparse.ArgumentParser(description="Terminal test CLI for the inbound agent graph")
    parser.add_argument(
        "--lang",
        default="hi-IN",
        choices=["hi-IN", "mr-IN", "en-IN"],
        help="Language hint passed to Agent 1 (default: hi-IN)",
    )
    args = parser.parse_args()
    asyncio.run(run(args.lang))


if __name__ == "__main__":
    main()
