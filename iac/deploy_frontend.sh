#!/usr/bin/env bash
# iac/deploy_frontend.sh — Deploy the Next.js frontend to Vercel (free tier).
# Usage:
#   ./iac/deploy_frontend.sh             # production deploy
#   ./iac/deploy_frontend.sh --preview   # preview deploy (unique URL, prod untouched)
#
# First run walks you through `vercel login` + project linking interactively.
# Env vars are pushed from your local .env so the dashboard needs no manual setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[deploy:frontend]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

PROD_FLAG="--prod"
for arg in "$@"; do
    case "$arg" in
        --preview) PROD_FLAG="" ;;
        --help|-h) echo "Usage: $0 [--preview]"; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

command -v vercel &>/dev/null || die "Vercel CLI not found. Install with: npm i -g vercel"

[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

# The frontend needs these baked in at build time.
[[ -n "${BACKEND_URL:-}" ]]  || die "BACKEND_URL not set in .env (your Render URL, e.g. https://hospital-receptionist.onrender.com)"
[[ -n "${LIVEKIT_URL:-}" ]]  || die "LIVEKIT_URL not set in .env"

cd "$ROOT/frontend"

# ── Sync build-time env vars to Vercel (idempotent: remove then re-add) ──────
ENV_TARGET="production"
[[ -z "$PROD_FLAG" ]] && ENV_TARGET="preview"

sync_env() {
    local key=$1 value=$2
    vercel env rm "$key" "$ENV_TARGET" --yes &>/dev/null || true
    printf '%s' "$value" | vercel env add "$key" "$ENV_TARGET" &>/dev/null \
        && ok "Set $key ($ENV_TARGET)" \
        || warn "Could not set $key — is the project linked? Run 'vercel link' in frontend/ first."
}

info "Syncing environment variables to Vercel..."
sync_env NEXT_PUBLIC_BACKEND_URL "$BACKEND_URL"
sync_env NEXT_PUBLIC_LIVEKIT_URL "$LIVEKIT_URL"

# ── Deploy ────────────────────────────────────────────────────────────────────
info "Deploying to Vercel ${PROD_FLAG:+(production)}${PROD_FLAG:-(preview)}..."
DEPLOY_URL="$(vercel deploy $PROD_FLAG --yes 2>&1 | tail -n1)"
[[ "$DEPLOY_URL" == https://* ]] || die "Deploy failed — run 'vercel deploy $PROD_FLAG' manually to see the error."
ok "Deployed: $DEPLOY_URL"

# ── Smoke check ───────────────────────────────────────────────────────────────
info "Checking the deployment responds..."
for i in $(seq 1 12); do
    if curl -sf -o /dev/null "$DEPLOY_URL"; then
        ok "Frontend is live: $DEPLOY_URL"
        echo ""
        echo -e "  Patient UI: ${CYAN}$DEPLOY_URL${NC}"
        echo -e "  Admin:      ${CYAN}$DEPLOY_URL/admin${NC}"
        exit 0
    fi
    sleep 5
done
warn "Deployment not responding yet — check the Vercel dashboard."
