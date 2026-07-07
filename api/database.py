import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Supabase's dashboard gives a plain postgresql:// connection string, but the
# async engine needs the asyncpg driver spelled out. Normalize it here so
# either form works in .env.
_raw_url = os.environ["DATABASE_URL"]
if _raw_url.startswith("postgresql://"):
    _raw_url = _raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(_raw_url, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_database() -> None:
    """Create tables if they don't already exist."""
    from api.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
