# IAC — Bring Everything Up

Everything needed to provision, seed, run and deploy the hospital receptionist
stack lives in this folder. One `.env` at the **repo root** drives all of it.

## TL;DR — one command

```bash
cp .env.example .env       # fill in the Required vars below (once)
./iac/run_local.sh         # brings up EVERYTHING locally
```

That single script, in order:

1. Checks tools (`uv`, `node`, `npm`) and required env vars — fails fast with a clear message
2. Creates a Python 3.11 venv with **uv** and installs `requirements.txt`
3. Creates / verifies all 8 Supabase tables and **seeds demo data if the DB is empty**
   (15 doctors, 10 patients, slots, prescriptions, follow-ups, lab reports, bills)
4. Installs frontend npm dependencies
5. Starts three services in the background:
   - **FastAPI backend** → http://localhost:8000 (docs at `/docs`)
   - **LiveKit agent worker** → registers with LiveKit Cloud (voice pipeline)
   - **Next.js frontend** → http://localhost:3000 (admin at `/admin`)
6. Waits for the backend `/health` check to pass

```bash
./iac/run_local.sh --reset    # wipe + re-create + re-seed the DB, then start everything
./iac/run_local.sh --seed     # DB setup + seed only, no services
./iac/run_local.sh --stop     # take everything down (or just Ctrl+C)
```

Logs stream to `iac/.logs/{backend,livekit,frontend}.log`; PIDs live in `iac/.pids/`.
The script is idempotent — rerunning it is always safe.

## Environment variables

All of these go in `.env` at the repo root (`cp .env.example .env`). The run
script validates the **Required** ones on startup and refuses to boot without them.

### Required — `run_local.sh` will not start without these

| Variable | What it is | Where to get it |
|---|---|---|
| `SARVAM_API_KEY` | Sarvam AI key for STT / LLM / TTS / Translate | [dashboard.sarvam.ai](https://dashboard.sarvam.ai) |
| `DATABASE_URL` | Postgres connection string, **`postgresql+asyncpg://` scheme** | Supabase → Project Settings → Database |
| `LIVEKIT_URL` | `wss://…livekit.cloud` — WebRTC endpoint (browser + worker both use it) | LiveKit Cloud → Project → Settings |
| `LIVEKIT_API_KEY` | Server-side key for minting room tokens | LiveKit Cloud → Settings → Keys |
| `LIVEKIT_API_SECRET` | Secret paired with the key | same place |

### Required for full functionality (agents fail at runtime without them)

| Variable | Used by |
|---|---|
| `UPSTASH_REDIS_REST_URL` | Short-term memory: recent calls, sessions, language pref, slot cache |
| `UPSTASH_REDIS_REST_TOKEN` | same |
| `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY` | Supabase REST access |

### Optional — features degrade gracefully

| Variable | Enables |
|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | Outbound SMS / WhatsApp confirmations |
| `ON_CALL_DOCTOR_PHONE` | Escalation call target |
| `SLACK_WEBHOOK_URL` | Escalation alerts to Slack |
| `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT` | LangSmith tracing of the agent graph |
| `PORT` | Backend port (default `8000`) |
| `LOG_LEVEL` | Backend log verbosity (default `INFO`) |
| `FRONTEND_URL` | CORS origin for the deployed frontend |

### Deploy-only — used by the `deploy_*` scripts, not by local runs

| Variable | Used by | Purpose |
|---|---|---|
| `BACKEND_URL` | `deploy_frontend_vercel.sh` | Public backend URL (your ngrok URL) synced to Vercel as `NEXT_PUBLIC_BACKEND_URL` |
| `NGROK_DOMAIN` | `deploy_backend_ngrok.sh` | Optional reserved ngrok domain so the URL survives restarts |

### Frontend note

The browser only sees vars prefixed **`NEXT_PUBLIC_`**, and they are baked in
at **build time**. You never set them by hand:

- Locally, `run_local.sh` injects `NEXT_PUBLIC_BACKEND_URL` (localhost) and
  `NEXT_PUBLIC_LIVEKIT_URL` (from `LIVEKIT_URL`) when starting `next dev`.
- On Vercel, the deploy scripts sync both and trigger a redeploy.
  (Setting `LIVEKIT_URL` on Vercel does nothing for the browser — it must be
  the `NEXT_PUBLIC_` name.)

## Deploying

| Piece | Where | Command |
|---|---|---|
| Frontend | **Vercel** (project `swastha`, linked to GitHub) | `./iac/deploy_frontend_vercel.sh` — syncs env vars, then git-push triggers the build. `--direct` deploys from local files, `--env-only` just syncs vars. |
| Backend + agent worker | **Local machine via ngrok tunnel** | `./iac/deploy_backend_ngrok.sh --sync-vercel` — starts both processes, opens the tunnel, points the Vercel frontend at the new URL and redeploys. Needs a one-time `ngrok config add-authtoken <token>`. |
| Database | **Supabase** (managed) | Auto-provisioned by `db_setup.py` on every run; `supabase_schema.sql` is the reference schema for manual bootstrap. |
| Redis | **Upstash** (managed) | Nothing to provision — keys are created lazily with TTLs. |

## Database scripts

```bash
uv run python -m iac.db_setup               # safe/idempotent: create tables + seed only if empty
uv run python -m iac.db_setup --force-seed  # keep tables, re-run the seeder
uv run python -m iac.db_reset               # DESTRUCTIVE: drop all 8 tables, re-create, re-seed
```

## Files in this folder

| File | Purpose |
|---|---|
| `run_local.sh` | **The one script** — full local bring-up (see TL;DR) |
| `deploy_backend_ngrok.sh` | Backend + agent worker behind an ngrok HTTPS tunnel |
| `deploy_frontend_vercel.sh` | Vercel env sync + GitHub-push deploy |
| `db_setup.py` | Idempotent schema create + conditional seed |
| `db_reset.py` | Destructive drop + re-create + re-seed |
| `supabase_schema.sql` | Reference SQL for the Supabase SQL editor |
| `vercel.json` | Vercel project config (Next.js, deploy from `main`) |

## Gotchas

- **`.env` is sourced by bash** — it must contain only `KEY=value` lines and
  comments. A stray command in it will execute (this has bitten us before).
- **`DATABASE_URL` must use `postgresql+asyncpg://`**, not plain `postgresql://` —
  the backend uses SQLAlchemy's asyncpg driver.
- **Free-tier ngrok rotates its URL on every restart** — rerun
  `deploy_backend_ngrok.sh --sync-vercel` after restarting, or reserve a static
  domain and set `NGROK_DOMAIN`.
- **Vercel `NEXT_PUBLIC_*` changes need a redeploy** to take effect; the scripts
  handle that automatically.
- The LiveKit agent worker connects **outbound** to LiveKit Cloud — only the
  FastAPI HTTP API needs the ngrok tunnel; voice audio never touches it.
