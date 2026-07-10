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

## Deploy

### Backend → Railway

1. Push repo to GitHub
2. Create a new Railway project → "Deploy from GitHub"
3. Railway auto-detects Python via `requirements.txt`
4. Set env vars in Railway dashboard (copy from `.env`)
5. Railway uses `Procfile` (`uvicorn api.main:app`) to start the service
6. `iac/railway.toml` sets health check path to `/health`

### Frontend → Vercel

```bash
# Install Vercel CLI
npm i -g vercel

# From repo root
cd frontend
vercel --prod

# Set env vars in Vercel dashboard:
#   NEXT_PUBLIC_BACKEND_URL = https://your-railway-app.up.railway.app
#   NEXT_PUBLIC_LIVEKIT_URL = wss://your-project.livekit.cloud
```

`iac/vercel.json` points Vercel at the `frontend/` directory.

---

## Files

| File | Purpose |
|---|---|
| `run.sh` | Local dev launcher — Python + Node deps, DB, all 3 services |
| `db_reset.py` | Drop + re-create + seed the database |
| `supabase_schema.sql` | Raw SQL schema for manual Supabase setup / inspection |
| `railway.toml` | Railway deployment config (start command, health check) |
| `vercel.json` | Vercel deployment config (root dir, env vars) |

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| `uv` | ≥ 0.4 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `node` | ≥ 20 | https://nodejs.org |
| `npm` | ≥ 10 | bundled with Node |

Python version is pinned to 3.11 via `uv venv --python 3.11`.
