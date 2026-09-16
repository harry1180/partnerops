"""Ingestion background tasks (Phase 1): parse queued raw files, normalize to
canonical records, quarantine bad rows, update DQ stats."""

from __future__ import annotations

from app.tasks import celery_app


@celery_app.task(name="app.tasks.ingestion.process_file")
def process_file(file_id: str) -> str:
    raise NotImplementedError("Implemented in Phase 1 with the AWS CUR adapter")
