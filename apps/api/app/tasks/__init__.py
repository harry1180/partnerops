"""Celery application: Redis broker + Postgres results (engine URL reused).

Queues: default (general jobs), pricing (heavy billing runs), ingestion.
Beat schedule registers Phase 2+ jobs; Phase 0 keeps the skeleton real:
prune_sessions runs via API-side call and the beat entry documents intent.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "cloudpartnerops",
    broker=settings.redis_url,
    backend=settings.database_url.replace("postgresql+psycopg", "db+postgresql+psycopg"),
    include=["app.tasks.billing", "app.tasks.ingestion", "app.tasks.reports"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.tasks.billing.*": {"queue": "pricing"},
        "app.tasks.ingestion.*": {"queue": "ingestion"},
        "app.tasks.reports.*": {"queue": "default"},
    },
    beat_schedule={
        "nightly-session-prune": {
            "task": "app.tasks.billing.prune_sessions",
            "schedule": crontab(hour=3, minute=17),
        },
        "scheduled-reports": {
            "task": "app.tasks.reports.run_due_schedules",
            "schedule": 900.0,  # every 15 minutes; due-checking is per schedule
        },
    },
)
