"""Money utilities: fixed-precision Decimal math for the billing engine.

Rules (ADR 003):
- Never float. Parse only via Decimal(str).
- ENGINE_SCALE = 6 intermediate, invoice scale = 2.
- Rounding modes: half_up (default), half_even, down (truncate toward zero).
- Every rounding is explicit at the documented step — no implicit float
  formatting anywhere.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

ENGINE_SCALE = Decimal("0.000001")  # 6dp intermediate
INVOICE_SCALE = Decimal("0.01")     # 2dp presentation/settlement

_MODES = {
    "half_up": ROUND_HALF_UP,
    "half_down": ROUND_HALF_DOWN,
    "up_abs": ROUND_UP,          # away from zero
    "truncate": lambda: 0,       # placeholder replaced below
}
_TRUNCATE = ROUND_FLOOR


def D(value: Decimal | float | int | str | None, default: Decimal | None = None) -> Decimal:
    """Coerce to Decimal safely. Floats are rejected unless they are exact ints."""
    if value is None:
        if default is not None:
            return default
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("Money values must be Decimal or str, never float")
    return Decimal(str(value))


def q(value: Decimal, scale: Decimal = INVOICE_SCALE, mode: str = "half_up") -> Decimal:
    """Quantize to a fixed scale with an explicit rounding mode."""
    if mode == "truncate":
        # away-from-zero truncation at scale (money convention, symmetric for negatives)
        sign = -1 if value < 0 else 1
        abs_v = abs(value)
        truncated = (abs_v // scale) * scale
        return Decimal(sign) * truncated
    rounding = {
        "half_up": ROUND_HALF_UP,
        "half_down": ROUND_HALF_DOWN,
        "up_abs": ROUND_UP,
        "floor": ROUND_FLOOR,
        "ceil": ROUND_CEILING,
    }.get(mode, ROUND_HALF_UP)
    return value.quantize(scale, rounding=rounding)


def engine(value: Decimal) -> Decimal:
    """Normalize intermediate results to engine scale (kills representation
    drift from repeated quantized additions)."""
    return value.quantize(ENGINE_SCALE, rounding=ROUND_HALF_UP)


def money_str(value: Decimal | None) -> str:
    return "0.00" if value is None else f"{value:.2f}"


def pct_of(value: Decimal, pct: Decimal) -> Decimal:
    """pct is a percentage number (12.5 → 12.5%)."""
    return engine(value * pct / Decimal("100"))


def is_negative(value: Decimal) -> bool:
    return value < 0


def clamp_min_max(value: Decimal, minimum: Decimal | None, maximum: Decimal | None) -> Decimal:
    if minimum is not None and value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


ZERO = Decimal("0")
ONE = Decimal("1")
