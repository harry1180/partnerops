"""Connector ingestion tasks (Phase 3): due-connector scheduler.

Beat wakes this every 15 minutes. For enabled connectors whose next_due_at
has passed, it ingests the fixture period (synthetic mode — the only mode
that exists until live connectors ship) under each connector's own org scope
and records evidence (file, row counts, audit) on the connector row.
"""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.connectors.run_due_connectors")
def run_due_connectors() -> int:
    from app.core.db import SessionLocal
    from app.services import connectors

    async def _run() -> int:
        async with SessionLocal() as session:
            results = await connectors.run_due_connectors(session)
            return len(results)

    from app.core.asyncio_util import run_async

    return run_async(_run())
