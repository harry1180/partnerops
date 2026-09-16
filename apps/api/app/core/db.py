"""Database engine and session management.

Two roles are used:
- app role (partnerops) — the application connects as a NON-superuser so
  PostgreSQL row-level security policies apply.
- migration role (partnerops_migrate) — owns the schema; used by Alembic.
RLS policies key off the session GUC `app.current_org_path`; see db/rls.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()

engine_kwargs: dict = {"pool_pre_ping": True}
if settings.database_url.startswith("postgresql"):
    engine_kwargs.update(pool_size=10, max_overflow=20)
else:
    # sqlite (tests): never pool connections across event loops
    from sqlalchemy.pool import NullPool

    engine_kwargs["poolclass"] = NullPool

engine = create_async_engine(settings.database_url, **engine_kwargs)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
