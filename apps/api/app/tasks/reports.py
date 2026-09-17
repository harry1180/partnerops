"""Report generation tasks (Phase 2): scheduled report runner.

Beat wakes this every 15 minutes; it processes due ReportSchedule rows:
generate CSV -> object storage -> ExportJob (audited) -> notification outbox.
"""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.reports.run_due_schedules")
def run_due_schedules() -> int:
    from app.core.db import SessionLocal
    from app.services import schedules

    async def _run() -> int:
        async with SessionLocal() as session:
            results = await schedules.run_due_schedules(session)
            return len(results)

    from app.core.asyncio_util import run_async

    return run_async(_run())
