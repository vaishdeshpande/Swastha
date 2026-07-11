"""iac/db_setup.py — Create all tables and seed demo data if the DB is empty.

Idempotent: safe to run on every startup.
Run with:  uv run python -m iac.db_setup
Force re-seed even if data exists:  uv run python -m iac.db_setup --force-seed
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is importable regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from sqlalchemy import select

from api.database import async_session, init_database
from api.models import Doctor


async def has_seed_data() -> bool:
    async with async_session() as s:
        result = await s.execute(select(Doctor).limit(1))
        return result.first() is not None


async def main(force_seed: bool = False) -> None:
    print("[db_setup] Creating / verifying tables...")
    await init_database()
    print("[db_setup] Schema up to date.")

    if not force_seed and await has_seed_data():
        print("[db_setup] Seed data already present — skipping seed.")
        return

    print("[db_setup] Seeding demo data...")
    from api.seed import seed  # import after env is loaded

    await seed()
    print("[db_setup] Demo data seeded. Database is ready.")


if __name__ == "__main__":
    asyncio.run(main(force_seed="--force-seed" in sys.argv))
