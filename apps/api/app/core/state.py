"""Process-wide runtime handles (redis client) shared between the ASGI app
and API routes without circular imports."""

from __future__ import annotations

from typing import Any

STATE: dict[str, Any] = {}
