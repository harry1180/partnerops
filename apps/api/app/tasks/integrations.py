"""Webhook delivery + email transport sweeps (Phase 5).

Every 5 minutes: send queued webhook deliveries (HMAC-signed, retry-capped)
and flush queued notification emails through the local transport (see
app/services/webhooks.py). One session, two independent sweeps; either can
find nothing to do.
"""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.integrations.sweep")
def sweep_integrations() -> dict:
    from app.core.asyncio_util import run_async
    from app.core.db import SessionLocal
    from app.services.webhooks import deliver_due, flush_notifications

    async def _run() -> dict:
        out = {"webhooks": 0, "emails": 0}
        async with SessionLocal() as session:
            out["webhooks"] = await deliver_due(session)
            out["emails"] = await flush_notifications(session)
        return out

    return run_async(_run())
