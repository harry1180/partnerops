"""Billing/pricing background tasks. Phase 0 ships session pruning; pricing
run orchestration lands in Phase 1 as run_pricing (async job over PricingRun)."""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.billing.prune_sessions")
def prune_sessions() -> int:
    from app.core.db import SessionLocal
    from app.services import auth_service

    async def _run() -> int:
        async with SessionLocal() as session:
            n = await auth_service.prune_expired_sessions(session)
            await session.commit()
            return n

    from app.core.asyncio_util import run_async

    return run_async(_run())
