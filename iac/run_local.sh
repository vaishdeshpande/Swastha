#!/usr/bin/env bash
# iac/run_local.sh — Single entrypoint to set up and run the full stack locally with uv.
#
# Runs: FastAPI backend + LiveKit agent worker + Next.js frontend, plus DB setup/seed.
#
# Usage:
#   ./iac/run_local.sh            # full setup + start all services
#   ./iac/run_local.sh --reset    # drop & re-seed DB, then start all services
#   ./iac/run_local.sh --seed     # DB setup + seed only (no services)
#   ./iac/run_local.sh --stop     # kill all background processes
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

require_cmd() { command -v "$1" &>/dev/null || die "Required command not found: $1. Install it and retry."; }
require_env() { [[ -n "${!1:-}" ]] || die "Environment variable $1 is not set. Check $ENV_FILE"; }

stop_all() {
    info "Stopping all services..."
    for pid_file in "$PID_DIR"/*.pid; do
        [[ -f "$pid_file" ]] || continue
        local pid name
        pid=$(cat "$pid_file")
        name=$(basename "$pid_file" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" && ok "Stopped $name (pid $pid)" || warn "Could not stop $name (pid $pid)"
        fi
        rm -f "$pid_file"
    done
}

# ── arg parsing ───────────────────────────────────────────────────────────────
DO_RESET=false
DO_SEED_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --reset) DO_RESET=true ;;
        --seed)  DO_SEED_ONLY=true ;;
        --stop)  stop_all; exit 0 ;;
        --help|-h)
            echo "Usage: $0 [--reset|--seed|--stop]"
            echo "  (no flags)  Full setup + start backend, LiveKit agent, frontend"
            echo "  --reset     Drop, re-create and re-seed the DB, then start services"
            echo "  --seed      DB setup + seed only"
            echo "  --stop      Kill all running services"
            exit 0
            ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# ── env file ──────────────────────────────────────────────────────────────────
[[ -f "$ENV_FILE" ]] || die ".env not found at $ENV_FILE — copy .env.example and fill in values."
set -a; source "$ENV_FILE"; set +a

require_env SARVAM_API_KEY
require_env DATABASE_URL
require_env LIVEKIT_URL
require_env LIVEKIT_API_KEY
require_env LIVEKIT_API_SECRET

# ── tool checks ───────────────────────────────────────────────────────────────
require_cmd uv
require_cmd node
require_cmd npm

info "Tool versions: uv $(uv --version | awk '{print $2}'), node $(node --version), npm $(npm --version)"

# ── Python env via uv ─────────────────────────────────────────────────────────
info "Setting up Python environment with uv..."
cd "$ROOT"
uv venv --python 3.11 .venv 2>/dev/null || true
uv pip install -r requirements.txt --quiet
ok "Python dependencies installed"

UV_RUN=(uv run --python "$ROOT/.venv/bin/python")

# ── DB setup / seed ───────────────────────────────────────────────────────────
if $DO_RESET; then
    info "Resetting database (drop + re-create + seed)..."
    "${UV_RUN[@]}" python -m iac.db_reset
    ok "Database reset"
else
    info "Ensuring schema exists and demo data is seeded..."
    "${UV_RUN[@]}" python -m iac.db_setup
    ok "Database ready"
fi

if $DO_SEED_ONLY; then
    ok "Seed-only mode — done."
    exit 0
fi

# ── Frontend deps ─────────────────────────────────────────────────────────────
info "Installing frontend dependencies..."
cd "$ROOT/frontend"
npm install --silent
ok "Frontend dependencies installed"

# ── Start services ────────────────────────────────────────────────────────────
mkdir -p "$PID_DIR" "$LOG_DIR"
trap 'stop_all' INT TERM

# 1. FastAPI backend
info "Starting FastAPI backend on http://localhost:${PORT:-8000} ..."
cd "$ROOT"
"${UV_RUN[@]}" uvicorn api.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --reload \
    > "$LOG_DIR/backend.log" 2>&1 &
echo $! > "$PID_DIR/backend.pid"
ok "Backend started (pid $(cat "$PID_DIR/backend.pid")) — logs: iac/.logs/backend.log"

# 2. LiveKit agent worker
info "Starting LiveKit agent worker..."
"${UV_RUN[@]}" python voice/livekit_agent.py dev \
    > "$LOG_DIR/livekit.log" 2>&1 &
echo $! > "$PID_DIR/livekit.pid"
ok "LiveKit agent started (pid $(cat "$PID_DIR/livekit.pid")) — logs: iac/.logs/livekit.log"

# 3. Next.js frontend
info "Starting Next.js frontend on http://localhost:3000 ..."
cd "$ROOT/frontend"
NEXT_PUBLIC_BACKEND_URL="http://localhost:${PORT:-8000}" \
NEXT_PUBLIC_LIVEKIT_URL="$LIVEKIT_URL" \
PORT=3000 npm run dev \
    > "$LOG_DIR/frontend.log" 2>&1 &
echo $! > "$PID_DIR/frontend.pid"
ok "Frontend started (pid $(cat "$PID_DIR/frontend.pid")) — logs: iac/.logs/frontend.log"

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
echo -e "  Stop: ${YELLOW}./iac/run_local.sh --stop${NC}  or  Ctrl+C"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# Keep script alive so Ctrl+C triggers trap cleanup
wait
