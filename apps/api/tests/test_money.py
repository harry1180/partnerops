"""Money engine unit tests: Decimal precision, rounding, clamping."""

from decimal import Decimal

import pytest
from app.engine.money import D, ZERO, clamp_min_max, engine, pct_of, q


def test_rejects_float_money():
    with pytest.raises(TypeError):
        D(1.2345)  # type: ignore[arg-type]
    assert D("1.2345") == Decimal("1.2345")
    assert D(Decimal("2")) == Decimal("2")
    assert D(None) == ZERO


def test_quantize_half_up():
    assert q(Decimal("0.125")) == Decimal("0.13")
    assert q(Decimal("-0.125")) == Decimal("-0.13")  # half away from zero on negative too
    assert q(Decimal("10.004")) == Decimal("10.00")


def test_quantize_modes():
    assert q(Decimal("0.125"), mode="half_down") == Decimal("0.12")
    assert q(Decimal("0.121"), mode="up_abs") == Decimal("0.13")
    assert q(Decimal("-0.129"), mode="truncate") == Decimal("-0.12")
    assert q(Decimal("1.999"), mode="floor") == Decimal("1.99")


def test_no_float_drift_in_accumulation():
    total = Decimal("0")
    for _ in range(10):
        total += engine(Decimal("0.1"))
    assert total == Decimal("1.0")  # float math gives 0.9999999999999999


def test_pct_of_engine_scale():
    assert pct_of(Decimal("100.00"), Decimal("12.5")) == Decimal("12.500000")
    assert pct_of(Decimal("0.333"), Decimal("100")) == Decimal("0.333000")


def test_clamp():
    assert clamp_min_max(Decimal("5"), Decimal("10"), None) == Decimal("10")
    assert clamp_min_max(Decimal("50"), None, Decimal("40")) == Decimal("40")
    assert clamp_min_max(Decimal("-5"), None, Decimal("40")) == Decimal("-5")
