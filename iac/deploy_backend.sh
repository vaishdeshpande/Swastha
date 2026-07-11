#!/usr/bin/env bash
# iac/deploy_backend.sh — Deploy the backend (FastAPI + LiveKit agent worker) to Render (free tier).
# Usage:
#   ./iac/deploy_backend.sh              # push + trigger deploy (via deploy hook if configured)
#   ./iac/deploy_backend.sh --no-push    # skip git push, just trigger the deploy hook
#
# First-time setup lives in the header of iac/render.yaml (Blueprint via the Render dashboard).
# After that, set RENDER_DEPLOY_HOOK_URL in .env and this script is fully hands-off.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[deploy:backend]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

DO_PUSH=true
for arg in "$@"; do
    case "$arg" in
        --no-push) DO_PUSH=false ;;
        --help|-h) echo "Usage: $0 [--no-push]"; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

cd "$ROOT"

# ── Blueprint must live at repo root — sync it from iac/ ─────────────────────
if ! cmp -s "$ROOT/iac/render.yaml" "$ROOT/render.yaml" 2>/dev/null; then
    cp "$ROOT/iac/render.yaml" "$ROOT/render.yaml"
    ok "Synced iac/render.yaml → render.yaml (Render reads Blueprints from repo root)"
fi

# ── Push to GitHub (Render auto-deploys on push once the Blueprint is linked) ─
if $DO_PUSH; then
    if [[ -n "$(git status --porcelain)" ]]; then
        warn "Working tree has uncommitted changes — they will NOT be deployed."
        git status --short
        echo ""
        read -r -p "Continue and push committed work only? [y/N] " reply
        [[ "$reply" =~ ^[Yy]$ ]] || die "Aborted. Commit your changes and re-run."
    fi
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    info "Pushing $BRANCH to origin..."
    git push origin "$BRANCH"
    ok "Pushed"
fi

# ── Trigger the deploy hook (instant, no waiting on auto-deploy polling) ─────
if [[ -n "${RENDER_DEPLOY_HOOK_URL:-}" ]]; then
    info "Triggering Render deploy hook..."
    curl -sf -X POST "$RENDER_DEPLOY_HOOK_URL" > /dev/null \
        && ok "Deploy triggered" \
        || die "Deploy hook call failed — check RENDER_DEPLOY_HOOK_URL in .env"
else
    warn "RENDER_DEPLOY_HOOK_URL not set — relying on Render auto-deploy from the git push."
    warn "First deploy? Follow the setup steps in the header of iac/render.yaml."
fi

# ── Wait for /health (also wakes the free-tier instance from sleep) ──────────
if [[ -n "${BACKEND_URL:-}" ]]; then
    info "Waiting for $BACKEND_URL/health (free tier cold start can take ~1 min)..."
    for i in $(seq 1 60); do
        if curl -sf "$BACKEND_URL/health" > /dev/null 2>&1; then
            ok "Backend is live: $BACKEND_URL"
            exit 0
        fi
        sleep 5
    done
    warn "No healthy response after 5 min — check the deploy logs in the Render dashboard."
else
    info "Set BACKEND_URL in .env (e.g. https://hospital-receptionist.onrender.com) to health-check after deploy."
fi
