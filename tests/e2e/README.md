# End-to-End Agent Flow Tests

These tests drive the **full inbound LangGraph graph** (`inbound_graph.ainvoke`)
exactly as `livekit_agent.llm_node()` does in production.

## What is simulated

```
Patient text input
      ↓
state["messages"].append({"role": "user", "content": "<text>"})
      ↓
inbound_graph.ainvoke(state)   ← same call as in production
      ↓
Agent 1 → Agent 2 → Agent 3/4 → post_call
      ↓
state["messages"][-1]["content"]  ← what the patient would hear
```

## What is mocked

Only external I/O boundaries:
- **Sarvam LLM** (`client.chat.completions`) — returns scripted JSON decisions
- **Sarvam Translate** (`translate_text`) — passthrough (returns input unchanged)
- **Sarvam Language ID** (`sarvam_identify_language`) — returns "hi-IN" or "mr-IN"
- **Redis** (`redis_get`, `redis_set`, `save_*`) — in-memory dict or AsyncMock
- **DB tools** (`get_patient_record`, `book_slot`, etc.) — return scripted dicts
- **post_call_node** — stubbed (no recording, no Batch STT needed)
- **escalate_to_doctor** — AsyncMock (Slack/Twilio not needed)

## What is NOT mocked

- LangGraph graph structure (conditional edges, node order)
- Agent node logic (all Python code in agents/*.py runs for real)
- State mutations (fields set by one agent are read by the next)
- Routing decisions (route_after_intake, check_escalation, etc.)

## Running

```bash
source .venv/bin/activate
pytest tests/e2e/ -v -s   # -s shows print() output with state snapshots
```
