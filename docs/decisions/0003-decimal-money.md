# 0003 — Money is Decimal/NUMERIC — floats are a bug

Date: 2026-09-16 · Status: Accepted

## Context
Invoicing must be exact and explainable; float drift or ambiguous rounding is
indefensible in a financial product.

## Decision
- All persisted amounts are `NUMERIC(20,6)` (`app/db/money.py`); quantity and
  percentages likewise where fractional.
- Python uses `Decimal` exclusively. `D()` **raises TypeError on float
  input** so drift can't enter by accident (unit-tested).
- Two scales: engine scale 6dp (intermediates), invoice scale 2dp
  (presentation/settlement), with explicit rounding mode recorded on the
  contract version (default half-up).
- Wire format: decimal strings in JSON (`"123.45"`), never JSON numbers,
  end-to-end.

## Consequences
- JS display helpers use integer-cent BigInt math (`packages/shared`) and
  are display-only; all authoritative arithmetic is server-side.
- Slightly more verbose code; prevents an entire bug class.
