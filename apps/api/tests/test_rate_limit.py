"""Rate limiter tests: memory fallback and the Redis async pipeline path."""

from __future__ import annotations

import pytest

from app.services import rate_limit


class FakePipeline:
    def __init__(self, store: dict) -> None:
        self._store = store

    def incr(self, key: str) -> None:
        pass

    def expire(self, key: str, _s: int) -> None:
        pass

    async def execute(self) -> list[int]:
        self._store.setdefault("n", 0)
        self._store["n"] += 1
        return [self._store["n"], 1]


class FakeRedis:
    def __init__(self) -> None:
        self._store: dict = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self._store)


@pytest.mark.asyncio
async def test_memory_limiter_blocks_over_limit():
    rate_limit.reset_memory_limiter()
    for _ in range(10):
        ok, _ = await rate_limit.allow_login_attempt(None, "1.2.3.4|a@b.com")
        assert ok
    ok, retry = await rate_limit.allow_login_attempt(None, "1.2.3.4|a@b.com")
    assert ok is False
    assert retry == 60
    # different identifier unaffected
    ok2, _ = await rate_limit.allow_login_attempt(None, "1.2.3.4|c@d.com")
    assert ok2 is True


@pytest.mark.asyncio
async def test_redis_pipeline_is_awaited_and_enforces():
    r = FakeRedis()
    for _ in range(10):
        ok, _ = await rate_limit.allow_login_attempt(r, "ip|user")
        assert ok
    ok, retry = await rate_limit.allow_login_attempt(r, "ip|user")
    assert ok is False
    assert retry == 60
