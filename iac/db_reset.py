"""iac/db_reset.py — Drop all tables and re-seed with fresh demo data.

Run with:  python -m iac.db_reset
Called by: iac/run.sh --reset
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is importable regardless of cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from sqlalchemy import text

from api.database import engine, init_database
from api.models import Base


async def drop_all() -> None:
    print("[db_reset] Dropping all tables...")
    async with engine.begin() as conn:
        # Drop in reverse dependency order to avoid FK violations
        await conn.execute(text("DROP TABLE IF EXISTS call_logs CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS discharge_followups CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS bills CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS lab_reports CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS prescriptions CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS appointments CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS patients CASCADE"))
        await conn.execute(text("DROP TABLE IF EXISTS doctors CASCADE"))
    print("[db_reset] All tables dropped.")


async def main() -> None:
    await drop_all()
    await init_database()
    print("[db_reset] Tables re-created.")

    # Import seed here (after env is loaded) to avoid circular imports at module level
    from api.seed import seed
    await seed()
    print("[db_reset] Demo data seeded. Database is ready.")


if __name__ == "__main__":
    asyncio.run(main())
