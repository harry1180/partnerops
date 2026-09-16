"""Celery worker entrypoint package for `celery -A workers` usage."""

from app.tasks import celery_app  # noqa: F401

__all__ = ["celery_app"]
