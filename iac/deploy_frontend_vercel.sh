#!/usr/bin/env bash
# iac/deploy_frontend_vercel.sh — Deploy the Next.js frontend to Vercel.
#
# The Vercel project is linked to this GitHub repo, so the normal deploy path
# is simply pushing to the production branch — Vercel builds automatically.
# This script:
#   1. Ensures the Vercel project is linked (vercel link) and connected to GitHub
#   2. Syncs build-time env vars (NEXT_PUBLIC_BACKEND_URL, NEXT_PUBLIC_LIVEKIT_URL)
#      from your local .env to the Vercel dashboard
#   3. Deploys — by pushing the current branch to GitHub (default),
#      or directly from the CLI with --direct
#
# Usage:
#   ./iac/deploy_frontend_vercel.sh             # env sync + git push (GitHub → Vercel auto-deploy)
#   ./iac/deploy_frontend_vercel.sh --direct    # env sync + `vercel deploy --prod` from local files
#   ./iac/deploy_frontend_vercel.sh --env-only  # only sync env vars, no deploy
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/.env"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${CYAN}[deploy:frontend]${NC} $*"; }
ok()    { echo -e "${GREEN}[ok]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
die()   { echo -e "${RED}[error]${NC} $*" >&2; exit 1; }

MODE="git"
for arg in "$@"; do
    case "$arg" in
        --direct)   MODE="direct" ;;
        --env-only) MODE="env" ;;
        --help|-h)  echo "Usage: $0 [--direct|--env-only]"; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

command -v vercel &>/dev/null || die "Vercel CLI not found. Install with: npm i -g vercel"

[[ -f "$ENV_FILE" ]] && { set -a; source "$ENV_FILE"; set +a; }

# The frontend needs these baked in at build time.
# BACKEND_URL is your public backend URL (e.g. the ngrok URL printed by
# ./iac/deploy_backend_ngrok.sh).
[[ -n "${BACKEND_URL:-}" ]] || die "BACKEND_URL not set in .env (e.g. your ngrok URL: https://xxxx.ngrok-free.app)"
[[ -n "${LIVEKIT_URL:-}" ]] || die "LIVEKIT_URL not set in .env"

cd "$ROOT/frontend"

# ── Link project (first run only) ─────────────────────────────────────────────
if [[ ! -d .vercel ]]; then
    info "Project not linked yet — running 'vercel link' (interactive, first run only)..."
    vercel link
fi

# ── Ensure GitHub is connected for push-to-deploy ─────────────────────────────
if [[ "$MODE" == "git" ]]; then
    info "Ensuring the Vercel project is connected to this GitHub repo..."
    vercel git connect --yes &>/dev/null \
        && ok "GitHub connected — pushes to the production branch auto-deploy" \
        || warn "Could not verify GitHub connection (may already be connected via the dashboard)."
fi

# ── Sync build-time env vars (idempotent: remove then re-add) ─────────────────
sync_env() {
    local key=$1 value=$2
    vercel env rm "$key" production --yes &>/dev/null || true
    printf '%s' "$value" | vercel env add "$key" production &>/dev/null \
        && ok "Set $key (production)" \
        || warn "Could not set $key — is the project linked?"
}

info "Syncing environment variables to Vercel..."
sync_env NEXT_PUBLIC_BACKEND_URL "$BACKEND_URL"
sync_env NEXT_PUBLIC_LIVEKIT_URL "$LIVEKIT_URL"

[[ "$MODE" == "env" ]] && { ok "Env-only mode — done."; exit 0; }

# ── Deploy ────────────────────────────────────────────────────────────────────
if [[ "$MODE" == "git" ]]; then
    cd "$ROOT"
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
    if [[ -n "$(git status --porcelain)" ]]; then
        die "Working tree has uncommitted changes. Commit them, then rerun (git push triggers the Vercel build)."
    fi
    info "Pushing branch '$BRANCH' to GitHub — Vercel will build automatically..."
    git push origin "$BRANCH"
    ok "Pushed. Vercel is building from GitHub."
    echo -e "  Watch the build: ${CYAN}https://vercel.com/dashboard${NC}"
    [[ "$BRANCH" != "main" ]] && warn "You pushed '$BRANCH' — this creates a preview deploy. Merge to main for production."
else
    info "Deploying directly from local files (vercel deploy --prod)..."
    DEPLOY_URL="$(vercel deploy --prod --yes 2>&1 | tail -n1)"
    [[ "$DEPLOY_URL" == https://* ]] || die "Deploy failed — run 'vercel deploy --prod' manually to see the error."
    ok "Deployed: $DEPLOY_URL"

    info "Checking the deployment responds..."
    for i in $(seq 1 12); do
        if curl -sf -o /dev/null "$DEPLOY_URL"; then
            ok "Frontend is live: $DEPLOY_URL"
            echo -e "  Patient UI: ${CYAN}$DEPLOY_URL${NC}"
            echo -e "  Admin:      ${CYAN}$DEPLOY_URL/admin${NC}"
            exit 0
        fi
        sleep 5
    done
    warn "Deployment not responding yet — check the Vercel dashboard."
fi
