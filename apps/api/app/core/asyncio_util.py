"""Async helpers: Windows requires a Selector event loop for psycopg3 async
(uvicorn configures it itself; alembic/seed/celery tasks must too)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    if sys.platform == "win32":
        policy = asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined]
        asyncio.set_event_loop_policy(policy)
    return asyncio.run(coro)
