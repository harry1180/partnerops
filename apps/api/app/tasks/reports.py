"""Report generation tasks (Phase 2): scheduled + on-demand reports."""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.reports.generate")
def generate_report(report_kind: str, parameters: dict) -> str:
    raise NotImplementedError("Implemented in Phase 2 (scheduled reports)")
