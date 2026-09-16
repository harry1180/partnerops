"""Shared pytest fixtures.

- unit fixtures: no DB.
- `client` / `db_state`: full ASGI app against a temporary SQLite DB created
  via the real Alembic migration chain (RLS statements skip on non-PG; the
  docker stack exercises actual RLS in the pg integration test marked below).
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

TMP_DB = os.path.join(tempfile.gettempdir(), f"cpo_test_{uuid.uuid4().hex}.db")
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "unit-test-secret"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TMP_DB}"
os.environ["DATABASE_URL_MIGRATE"] = f"sqlite+aiosqlite:///{TMP_DB}"
os.environ["REDIS_URL"] = "redis://localhost:6399/0"  # absent → graceful
os.environ["OBJECT_STORAGE_ENDPOINT"] = "http://localhost:6999"

import app.core.db as db_mod  # noqa: E402  (env must precede import of settings)
from app.core.db import engine  # noqa: E402


def _run_migrations() -> None:
    import subprocess
    import sys

    api_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=api_root,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": api_root},
    )
    if r.returncode != 0:
        raise RuntimeError(f"alembic upgrade failed:\n{r.stdout}\n{r.stderr}")


@pytest.fixture(scope="session")
def migrated_db():
    """Migrate + seed the shared test DB exactly once, before any test runs."""
    _run_migrations()

    async def _seed() -> None:
        import app.seed as seed_mod

        await seed_mod.seed()

    import asyncio

    asyncio.run(_seed())
    yield
    engine.sync_engine.dispose()
    try:
        if os.path.exists(TMP_DB):
            os.remove(TMP_DB)
    except OSError:
        pass  # Windows file-handle races: temp file cleanup is best-effort


@pytest_asyncio.fixture
async def client() -> AsyncIterator:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with db_mod.SessionLocal() as s:
        yield s
