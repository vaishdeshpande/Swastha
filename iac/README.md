# IAC — Infrastructure as Code

Everything needed to provision, seed, run and deploy the hospital receptionist
stack lives in this folder. One `.env` at the repo root drives all of it
(copy `.env.example` and fill in values).

## Deployment topology

| Piece | Where | How |
|---|---|---|
| Frontend (Next.js) | **Vercel**, linked to GitHub | Push to `main` → Vercel auto-builds. `deploy_frontend_vercel.sh` syncs env vars + pushes. |
| Backend (FastAPI + LiveKit agent worker) | **Local machine, exposed via ngrok** | `deploy_backend_ngrok.sh` starts both processes and opens an HTTPS tunnel. |
| Database | **Supabase PostgreSQL** | Schema in `supabase_schema.sql`; auto-created + seeded by `db_setup.py`. |
| Short-term memory | **Upstash Redis** | No provisioning script needed — keys are created lazily with TTLs. |
| Voice infra | **LiveKit Cloud** | Managed; only needs `LIVEKIT_*` env vars. |

## Files

| File | Purpose |
|---|---|
| `run_local.sh` | Run **everything locally** with uv: DB setup + seed, FastAPI backend, LiveKit agent worker, Next.js frontend. |
| `deploy_backend_ngrok.sh` | Start backend + agent worker and expose them publicly through an ngrok tunnel. `--sync-vercel` pushes the tunnel URL to Vercel and redeploys the frontend. |
| `deploy_frontend_vercel.sh` | Sync `NEXT_PUBLIC_*` env vars to Vercel and deploy via GitHub push (or `--direct` for a CLI deploy). |
| `db_setup.py` | Idempotent: create all 8 tables, seed demo data only if the DB is empty. |
| `db_reset.py` | Destructive: drop all tables, re-create, re-seed fresh demo data. |
| `supabase_schema.sql` | Reference schema — paste into the Supabase SQL editor for manual bootstrap/inspection. |
| `vercel.json` | Vercel project config (Next.js framework, deploy from `main`). |

## Quick start

### 1. Run everything locally

```bash
cp .env.example .env       # fill in Sarvam, LiveKit, Supabase, Upstash keys
./iac/run_local.sh         # installs deps (uv + npm), sets up DB, starts all 3 services
```

- Frontend: http://localhost:3000 · Admin: http://localhost:3000/admin
- Backend: http://localhost:8000 · Docs: http://localhost:8000/docs
- Logs in `iac/.logs/`, stop with `./iac/run_local.sh --stop` or Ctrl+C

Flags: `--reset` (drop + re-seed DB first), `--seed` (DB only), `--stop`.

### 2. Deploy backend (ngrok)

```bash
brew install ngrok
ngrok config add-authtoken <your-token>
./iac/deploy_backend_ngrok.sh --sync-vercel
```

Prints a public `https://….ngrok-free.app` URL and, with `--sync-vercel`,
points the Vercel frontend at it and triggers a redeploy. Set `NGROK_DOMAIN`
in `.env` if you have a reserved static domain (URL then survives restarts).

### 3. Deploy frontend (Vercel ⇄ GitHub)

```bash
npm i -g vercel
./iac/deploy_frontend_vercel.sh
```

First run walks through `vercel link` interactively and connects the GitHub
repo. After that, deploys are just commits pushed to `main` — the script
syncs `NEXT_PUBLIC_BACKEND_URL` / `NEXT_PUBLIC_LIVEKIT_URL` from your `.env`
and pushes the current branch. Use `--direct` to deploy from local files
without going through GitHub, `--env-only` to only sync env vars.

### 4. Database only

```bash
uv run python -m iac.db_setup            # create tables + seed if empty
uv run python -m iac.db_setup --force-seed
uv run python -m iac.db_reset            # DESTRUCTIVE: drop + re-create + re-seed
```

## Gotchas

- **ngrok free tier** rotates the URL on every restart — rerun with
  `--sync-vercel` after a restart, or reserve a static domain and set `NGROK_DOMAIN`.
- **Vercel env vars are build-time** (`NEXT_PUBLIC_*`): changing them requires
  a redeploy, which both scripts handle for you.
- The LiveKit agent worker connects **outbound** to LiveKit Cloud, so it needs
  no tunnel — only the FastAPI HTTP API goes through ngrok.
