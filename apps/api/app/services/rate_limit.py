"""Login rate limiting (per IP+email) with Redis and an in-process fallback so
local development and tests work without Redis."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from app.core.config import get_settings

_lock: bool = False


class _MemoryLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    def clear(self) -> None:
        self._hits.clear()


_memory = _MemoryLimiter()


async def allow_login_attempt(redis_client: object | None, identifier: str) -> tuple[bool, int]:
    """Returns (allowed, retry_after_seconds). identifier = f"{ip}|{email}"."""
    settings = get_settings()
    limit = settings.login_rate_limit_per_minute
    window = 60
    if redis_client is not None:
        try:
            key = f"rl:login:{identifier}"
            pipe = redis_client.pipeline()  # type: ignore[attr-defined]
            pipe.incr(key)
            pipe.expire(key, window)
            count, _ = await pipe.execute()  # type: ignore[attr-defined]
            if int(count) > limit:
                return False, window
            return True, 0
        except Exception:  # pragma: no cover — redis blips must not lock users out
            pass
    ok = _memory.check(identifier, limit, window)
    return (ok, 0 if ok else window)


def reset_memory_limiter() -> None:  # tests
    _memory.clear()
