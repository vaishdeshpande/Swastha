#!/usr/bin/env bash
# iac/deploy.sh — Deploy the full stack: backend to Render, then frontend to Vercel.
# Usage:
#   ./iac/deploy.sh                # deploy both
#   ./iac/deploy.sh --backend     # backend only
#   ./iac/deploy.sh --frontend    # frontend only
#
# Everything else (Supabase, Upstash, LiveKit Cloud, Sarvam) is managed
# infrastructure — nothing to deploy, just env vars. See iac/README.md.
set -euo pipefail

IAC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

DO_BACKEND=true
DO_FRONTEND=true
for arg in "$@"; do
    case "$arg" in
        --backend)  DO_FRONTEND=false ;;
        --frontend) DO_BACKEND=false ;;
        --help|-h)  echo "Usage: $0 [--backend|--frontend]"; exit 0 ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

if $DO_BACKEND; then
    echo -e "${CYAN}━━━ Backend → Render ━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    bash "$IAC/deploy_backend.sh"
    echo ""
fi

if $DO_FRONTEND; then
    echo -e "${CYAN}━━━ Frontend → Vercel ━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    bash "$IAC/deploy_frontend.sh"
    echo ""
fi

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Deploy complete${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
