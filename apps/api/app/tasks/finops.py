"""FinOps/governance scheduled passes (Phase 4).

Nightly (beat 03:40 UTC): for every partner org that has any canonical cost
data, run the anomaly pass, the recommendation pass + savings realization,
and the governance evaluation. Same worker pattern as connectors:
bypass-scope discovery of orgs, then per-org scoped processing; each org
commits separately so one bad tenant can't poison the run. All results are
audited with actor_kind=system.
"""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.finops.run_finops_passes")
def run_finops_passes() -> int:
    from app.core.db import SessionLocal
    from app.services.finops_passes import run_all_org_passes

    async def _run() -> int:
        async with SessionLocal() as session:
            return await run_all_org_passes(session)

    from app.core.asyncio_util import run_async

    return run_async(_run())
