# iac/ — Infrastructure as Code

One folder, one command. Everything needed to run and deploy the Hospital Receptionist stack.

---

## Local development

```bash
# First time
cp .env.example .env       # fill in your API keys
chmod +x iac/run.sh
./iac/run.sh               # installs deps, migrates DB, seeds, starts all 3 services
```

```bash
# Re-seed (wipe DB + fresh demo data, then start)
./iac/run.sh --reset

# Seed only (no restart)
./iac/run.sh --seed

# Stop all background services
./iac/run.sh --stop
```

Services started:

| Service | URL | Log |
|---|---|---|
| Next.js frontend | http://localhost:3000 | iac/.logs/frontend.log |
| FastAPI backend | http://localhost:8000 | iac/.logs/backend.log |
| LiveKit agent worker | (connects to LiveKit Cloud) | iac/.logs/livekit.log |

---

## Database

### Auto-managed (default)
`run.sh` calls SQLAlchemy `create_all()` on every start — idempotent, safe to run repeatedly.

### Manual reset (Supabase UI)
Copy-paste `iac/supabase_schema.sql` into the Supabase SQL Editor and run it to recreate all 8 tables from scratch. Useful when you need a clean slate on the hosted DB.

### db_reset.py
```bash
python -m iac.db_reset     # drop all tables + re-create + seed
```

---

## Deploy (all free tiers)

```bash
./iac/deploy.sh              # backend → Render, frontend → Vercel
./iac/deploy.sh --backend    # backend only
./iac/deploy.sh --frontend   # frontend only
```

Supabase, Upstash, LiveKit Cloud, and Sarvam are managed services — nothing to
deploy there, only env vars.

### Backend → Render (free tier)

`iac/start_production.sh` is the production start command — it runs migrations,
starts the **LiveKit agent worker in the background**, and uvicorn in the
foreground (the local `Procfile`/`railway.toml` only start uvicorn).

First-time setup:

1. `./iac/deploy_backend.sh` — copies `iac/render.yaml` to the repo root
   (Render only reads Blueprints from root) and pushes to GitHub
2. [dashboard.render.com](https://dashboard.render.com) → New → **Blueprint** → select this repo
3. Fill in the secret env vars (values from your local `.env`)
4. Optional but recommended: Settings → **Deploy Hook** → copy the URL into
   `RENDER_DEPLOY_HOOK_URL` in `.env`; also set `BACKEND_URL` to your
   `https://….onrender.com` URL so the script health-checks after deploy

After that, every deploy is just `./iac/deploy_backend.sh`.

> Free-tier caveat: the instance sleeps after 15 min idle (~50s cold start).
> Hit `$BACKEND_URL/health` before a demo, or ping it every 10 min with a free
> cron service (e.g. cron-job.org) to keep it warm.

### Frontend → Vercel (free tier)

```bash
npm i -g vercel
cd frontend && vercel login && vercel link   # first time only
./iac/deploy_frontend.sh                     # prod deploy
./iac/deploy_frontend.sh --preview           # preview URL, prod untouched
```

The script pushes `NEXT_PUBLIC_BACKEND_URL` (from `BACKEND_URL`) and
`NEXT_PUBLIC_LIVEKIT_URL` (from `LIVEKIT_URL`) out of your local `.env` into
Vercel, so no dashboard configuration is needed.

### Railway (legacy, paid)

`iac/railway.toml` + `Procfile` remain if you prefer Railway's $5/mo credit —
but note they start only the API, not the agent worker.

---

## Files

| File | Purpose |
|---|---|
| `run.sh` | Local dev launcher — Python + Node deps, DB, all 3 services |
| `deploy.sh` | Deploy everything: backend → Render, frontend → Vercel |
| `deploy_backend.sh` | Backend deploy: sync Blueprint to root, push, trigger hook, health-check |
| `deploy_frontend.sh` | Frontend deploy: sync env vars to Vercel, deploy, smoke-check |
| `start_production.sh` | Production start command — agent worker + uvicorn in one container |
| `render.yaml` | Render Blueprint (source of truth — auto-copied to repo root) |
| `db_reset.py` | Drop + re-create + seed the database |
| `supabase_schema.sql` | Raw SQL schema for manual Supabase setup / inspection |
| `railway.toml` | Railway deployment config (legacy — API only, no agent worker) |
| `vercel.json` | Vercel deployment config (root dir, env vars) |

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| `uv` | ≥ 0.4 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `node` | ≥ 20 | https://nodejs.org |
| `npm` | ≥ 10 | bundled with Node |

Python version is pinned to 3.11 via `uv venv --python 3.11`.
