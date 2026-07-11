#!/usr/bin/env bash
# iac/start_production.sh — Production entrypoint (Render / Railway / any container host).
# Runs BOTH processes the backend needs in one container:
#   1. LiveKit agent worker (background)  — voice/livekit_agent.py start
#   2. FastAPI via uvicorn   (foreground) — serves /health for the platform health check
#
# Used as the start command by iac/render.yaml. No flags.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "[start] Running DB migrations (create_all)..."
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from api.database import init_database
asyncio.run(init_database())
print('Tables created / verified.')
"

echo "[start] Starting LiveKit agent worker (background)..."
python voice/livekit_agent.py start &
AGENT_PID=$!

# If uvicorn exits (or the platform sends SIGTERM), take the agent down with it.
trap 'kill "$AGENT_PID" 2>/dev/null || true' EXIT INT TERM

echo "[start] Starting FastAPI on port ${PORT:-8000}..."
uvicorn api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
