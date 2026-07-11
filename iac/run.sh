#!/usr/bin/env bash
# iac/run.sh — Single entrypoint to set up and run the full stack locally.
# Usage:
#   ./iac/run.sh            # full setup + start all services
#   ./iac/run.sh --reset    # drop & re-seed DB, then start all services
#   ./iac/run.sh --seed     # seed DB only (no restart)
#   ./iac/run.sh --stop     # kill all background processes
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IAC="$ROOT/iac"
PID_DIR="$IAC/.pids"
LOG_DIR="$IAC/.logs"
ENV_FILE="$ROOT/.env"

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[iac]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

# ── helpers ───────────────────────────────────────────────────────────────────
require_cmd() { command -v "$1" &>/dev/null || die "Required command not found: $1. Install it and retry."; }
require_env()  { [[ -n "${!1:-}" ]] || die "Environment variable $1 is not set. Check $ENV_FILE"; }

stop_all() {
    info "Stopping all services..."
    for pid_file in "$PID_DIR"/*.pid; do
        [[ -f "$pid_file" ]] || continue
        local pid; pid=$(cat "$pid_file")
        local name; name=$(basename "$pid_file" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && ok "Stopped $name (pid $pid)" || warn "Could not stop $name (pid $pid)"
        fi
        rm -f "$pid_file"
    done
}

# ── arg parsing ───────────────────────────────────────────────────────────────
DO_RESET=false
DO_SEED_ONLY=false
DO_STOP=false

for arg in "$@"; do
    case "$arg" in
        --reset)     DO_RESET=true ;;
        --seed)      DO_SEED_ONLY=true ;;
        --stop)      DO_STOP=true ;;
        --help|-h)
            echo "Usage: $0 [--reset|--seed|--stop]"
            echo "  (no flags)  Full setup + start all services"
            echo "  --reset     Re-seed the DB then start all services"
            echo "  --seed      Seed DB only"
            echo "  --stop      Kill all running services"
            exit 0
            ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── stop shortcut ─────────────────────────────────────────────────────────────
if $DO_STOP; then
    stop_all
    exit 0
fi

# ── env file ──────────────────────────────────────────────────────────────────
[[ -f "$ENV_FILE" ]] || die ".env not found at $ENV_FILE — copy .env.example and fill in values."
set -a; source "$ENV_FILE"; set +a

# Validate required secrets
require_env SARVAM_API_KEY
require_env DATABASE_URL
require_env LIVEKIT_URL
require_env LIVEKIT_API_KEY
require_env LIVEKIT_API_SECRET

# ── tool checks ───────────────────────────────────────────────────────────────
require_cmd uv
require_cmd node
require_cmd npm

info "Tool versions:"
uv --version
node --version
npm --version

# ── Python venv via uv ────────────────────────────────────────────────────────
info "Setting up Python environment with uv..."
cd "$ROOT"
uv venv --python 3.11 .venv 2>/dev/null || true
uv pip install -r requirements.txt --quiet
ok "Python dependencies installed"

PYTHON="$ROOT/.venv/bin/python"

# ── Frontend deps ─────────────────────────────────────────────────────────────
info "Installing frontend dependencies..."
cd "$ROOT/frontend"
npm install --silent
ok "Frontend dependencies installed"

# ── DB setup & seed ───────────────────────────────────────────────────────────
cd "$ROOT"

if $DO_RESET; then
    info "Resetting database (dropping and re-seeding)..."
    $PYTHON -m iac.db_reset
    ok "Database reset"
fi

info "Running DB migrations (create_all)..."
$PYTHON -c "
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from api.database import init_database
asyncio.run(init_database())
print('Tables created / verified.')
"
ok "Database schema up to date"

if $DO_SEED_ONLY; then
    info "Seeding demo data..."
    $PYTHON -m api.seed
    ok "Seed complete"
    exit 0
fi

# Seed only if tables are empty
info "Checking if seed data exists..."
$PYTHON -c "
import asyncio, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()
from api.database import async_session
from api.models import Doctor
from sqlalchemy import select

async def check():
    async with async_session() as s:
        result = await s.execute(select(Doctor).limit(1))
        row = result.first()
        if not row:
            print('EMPTY')
        else:
            print('HAS_DATA')

asyncio.run(check())
" | grep -q "EMPTY" && {
    info "No seed data found — seeding..."
    $PYTHON -m api.seed
    ok "Database seeded with demo data"
} || ok "Seed data already present — skipping"

# ── Start services ─────────────────────────────────────────────────────────────
mkdir -p "$PID_DIR" "$LOG_DIR"

# Trap to clean up on exit
trap 'stop_all' INT TERM

# 1. FastAPI backend
info "Starting FastAPI backend on http://localhost:${PORT:-8000} ..."
cd "$ROOT"
"$ROOT/.venv/bin/uvicorn" api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --reload \
    > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > "$PID_DIR/backend.pid"
ok "Backend started (pid $BACKEND_PID) — logs: iac/.logs/backend.log"

# 2. LiveKit agent worker
info "Starting LiveKit agent worker..."
cd "$ROOT"
"$ROOT/.venv/bin/python" voice/livekit_agent.py dev \
    > "$LOG_DIR/livekit.log" 2>&1 &
LIVEKIT_PID=$!
echo "$LIVEKIT_PID" > "$PID_DIR/livekit.pid"
ok "LiveKit agent started (pid $LIVEKIT_PID) — logs: iac/.logs/livekit.log"

# 3. Next.js frontend
info "Starting Next.js frontend on http://localhost:3000 ..."
cd "$ROOT/frontend"
PORT=3000 npm run dev \
    > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$PID_DIR/frontend.pid"
ok "Frontend started (pid $FRONTEND_PID) — logs: iac/.logs/frontend.log"

# ── Health wait ───────────────────────────────────────────────────────────────
info "Waiting for backend to be ready..."
for i in $(seq 1 30); do
    if curl -sf "http://localhost:${PORT:-8000}/health" > /dev/null 2>&1; then
        ok "Backend is healthy"
        break
    fi
    sleep 1
    [[ $i -eq 30 ]] && warn "Backend did not respond after 30s — check iac/.logs/backend.log"
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Hospital Receptionist — All services running   ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Frontend:  ${CYAN}http://localhost:3000${NC}"
echo -e "  Backend:   ${CYAN}http://localhost:${PORT:-8000}${NC}"
echo -e "  API docs:  ${CYAN}http://localhost:${PORT:-8000}/docs${NC}"
echo -e "  Admin:     ${CYAN}http://localhost:3000/admin${NC}"
echo -e ""
echo -e "  Logs: ${YELLOW}iac/.logs/{backend,livekit,frontend}.log${NC}"
echo -e "  Stop: ${YELLOW}./iac/run.sh --stop${NC}  or  Ctrl+C"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Keep script alive so Ctrl+C triggers trap cleanup
wait
