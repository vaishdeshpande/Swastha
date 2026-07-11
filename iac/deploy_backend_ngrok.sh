#!/usr/bin/env bash
# iac/deploy_backend_ngrok.sh — "Deploy" the backend by exposing the local
# FastAPI + LiveKit agent worker to the internet through an ngrok tunnel.
#
# What it does:
#   1. Ensures DB schema + seed data exist (via iac/db_setup.py)
#   2. Starts the FastAPI backend and the LiveKit agent worker locally (uv)
#   3. Opens an ngrok HTTPS tunnel to the backend port
#   4. Prints the public URL and (optionally) pushes it to Vercel as
#      NEXT_PUBLIC_BACKEND_URL so the deployed frontend talks to this tunnel.
#
# Usage:
#   ./iac/deploy_backend_ngrok.sh                 # start backend + tunnel
#   ./iac/deploy_backend_ngrok.sh --sync-vercel   # also update Vercel env + redeploy frontend
#   ./iac/deploy_backend_ngrok.sh --stop          # stop backend, agent and tunnel
#
# Requires: uv, ngrok (brew install ngrok) with an authtoken configured
#           (ngrok config add-authtoken <token>).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IAC="$ROOT/iac"
PID_DIR="$IAC/.pids"
LOG_DIR="$IAC/.logs"
ENV_FILE="$ROOT/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[deploy:backend]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

stop_all() {
    info "Stopping backend, agent worker and ngrok..."
    for name in backend livekit ngrok; do
        local pid_file="$PID_DIR/$name.pid"
        [[ -f "$pid_file" ]] || continue
        local pid; pid=$(cat "$pid_file")
        kill "$pid" 2>/dev/null && ok "Stopped $name (pid $pid)" || true
        rm -f "$pid_file"
    done
}

SYNC_VERCEL=false
for arg in "$@"; do
    case "$arg" in
        --sync-vercel) SYNC_VERCEL=true ;;
        --stop)        stop_all; exit 0 ;;
        --help|-h)     echo "Usage: $0 [--sync-vercel|--stop]"; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

command -v uv    &>/dev/null || die "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
command -v ngrok &>/dev/null || die "ngrok not found. Install: brew install ngrok, then: ngrok config add-authtoken <token>"
command -v curl  &>/dev/null || die "curl not found."

[[ -f "$ENV_FILE" ]] || die ".env not found at $ENV_FILE — copy .env.example and fill in values."
set -a; source "$ENV_FILE"; set +a
PORT="${PORT:-8000}"

mkdir -p "$PID_DIR" "$LOG_DIR"

# ── Python env + DB ───────────────────────────────────────────────────────────
cd "$ROOT"
info "Installing Python dependencies with uv..."
uv venv --python 3.11 .venv 2>/dev/null || true
uv pip install -r requirements.txt --quiet
UV_RUN=(uv run --python "$ROOT/.venv/bin/python")

info "Ensuring DB schema + seed data..."
"${UV_RUN[@]}" python -m iac.db_setup
ok "Database ready"

# ── Backend ───────────────────────────────────────────────────────────────────
if [[ -f "$PID_DIR/backend.pid" ]] && kill -0 "$(cat "$PID_DIR/backend.pid")" 2>/dev/null; then
    ok "Backend already running (pid $(cat "$PID_DIR/backend.pid"))"
else
    info "Starting FastAPI backend on port $PORT ..."
    "${UV_RUN[@]}" uvicorn api.main:app --host 0.0.0.0 --port "$PORT" \
        > "$LOG_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"
    ok "Backend started (pid $(cat "$PID_DIR/backend.pid"))"
fi

# ── LiveKit agent worker ──────────────────────────────────────────────────────
if [[ -f "$PID_DIR/livekit.pid" ]] && kill -0 "$(cat "$PID_DIR/livekit.pid")" 2>/dev/null; then
    ok "LiveKit agent already running (pid $(cat "$PID_DIR/livekit.pid"))"
else
    info "Starting LiveKit agent worker..."
    "${UV_RUN[@]}" python voice/livekit_agent.py start \
        > "$LOG_DIR/livekit.log" 2>&1 &
    echo $! > "$PID_DIR/livekit.pid"
    ok "LiveKit agent started (pid $(cat "$PID_DIR/livekit.pid"))"
fi

# ── Wait for backend health ───────────────────────────────────────────────────
info "Waiting for backend to be healthy..."
for i in $(seq 1 30); do
    curl -sf "http://localhost:$PORT/health" > /dev/null 2>&1 && { ok "Backend is healthy"; break; }
    sleep 1
    [[ $i -eq 30 ]] && die "Backend did not become healthy — check iac/.logs/backend.log"
done

# ── ngrok tunnel ──────────────────────────────────────────────────────────────
if [[ -f "$PID_DIR/ngrok.pid" ]] && kill -0 "$(cat "$PID_DIR/ngrok.pid")" 2>/dev/null; then
    ok "ngrok already running (pid $(cat "$PID_DIR/ngrok.pid"))"
else
    info "Opening ngrok tunnel to port $PORT ..."
    NGROK_ARGS=(http "$PORT" --log stdout)
    # Use a stable reserved domain if provided (paid/free-static plans)
    [[ -n "${NGROK_DOMAIN:-}" ]] && NGROK_ARGS+=(--url "$NGROK_DOMAIN")
    ngrok "${NGROK_ARGS[@]}" > "$LOG_DIR/ngrok.log" 2>&1 &
    echo $! > "$PID_DIR/ngrok.pid"
fi

# Fetch the public URL from ngrok's local API
PUBLIC_URL=""
for i in $(seq 1 20); do
    PUBLIC_URL=$(curl -sf http://127.0.0.1:4040/api/tunnels 2>/dev/null \
        | "${UV_RUN[@]}" python -c "import sys,json; t=json.load(sys.stdin).get('tunnels',[]); print(next((x['public_url'] for x in t if x['public_url'].startswith('https')), ''))" \
        2>/dev/null || true)
    [[ -n "$PUBLIC_URL" ]] && break
    sleep 1
done
[[ -n "$PUBLIC_URL" ]] || die "Could not read ngrok public URL — check iac/.logs/ngrok.log"
ok "Backend is publicly reachable at: $PUBLIC_URL"

# Smoke check through the tunnel
curl -sf "$PUBLIC_URL/health" > /dev/null 2>&1 \
    && ok "Health check through tunnel passed: $PUBLIC_URL/health" \
    || warn "Tunnel health check failed — free ngrok may show a browser interstitial; API calls still work."

# ── Optionally sync URL to Vercel ─────────────────────────────────────────────
if $SYNC_VERCEL; then
    command -v vercel &>/dev/null || die "Vercel CLI not found. Install: npm i -g vercel"
    info "Pushing NEXT_PUBLIC_BACKEND_URL=$PUBLIC_URL to Vercel (production)..."
    cd "$ROOT/frontend"
    vercel env rm NEXT_PUBLIC_BACKEND_URL production --yes &>/dev/null || true
    printf '%s' "$PUBLIC_URL" | vercel env add NEXT_PUBLIC_BACKEND_URL production &>/dev/null \
        || die "Could not set env var — run 'vercel link' in frontend/ first."
    ok "Vercel env updated"
    info "Triggering production redeploy so the new URL is baked in..."
    vercel deploy --prod --yes > /dev/null && ok "Frontend redeployed with new backend URL"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Backend deployed via ngrok                      ${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  Public URL: ${CYAN}$PUBLIC_URL${NC}"
echo -e "  API docs:   ${CYAN}$PUBLIC_URL/docs${NC}"
echo -e "  Inspector:  ${CYAN}http://127.0.0.1:4040${NC}"
echo -e ""
$SYNC_VERCEL || echo -e "  ${YELLOW}Tip:${NC} rerun with ${YELLOW}--sync-vercel${NC} to point the Vercel frontend at this URL."
echo -e "  Stop: ${YELLOW}./iac/deploy_backend_ngrok.sh --stop${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
