"""Budgets, variance and forecasts (Phase 4).

Budget = scope (customer / family / account / partner-org) × period × amount.
Actual = sum of canonical provider-billed rows whose billing period falls in
the budget window (usage+support+tax+adjustment+credit so credits net off).

Variance carries an honest burn projection: when a month is partially
elapsed, `projected_total` = spend + average-daily-burn × remaining days
(method recorded; this is a straight-line burn assumption, not a forecast
model). Budget breaches feed `budget_violation` governance-style alerts
(over-threshold status is computed, never stored as fact).

Forecast (org-level): monthly average MoM growth over observed months,
projected N months forward — labeled `avg_mom_growth_linear` so nobody
mistakes it for ML.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_scope
from app.models.cost import CanonicalCostRecord
from app.models.finops import Budget

CENT = Decimal("0.01")
SPEND_TYPES = ("usage", "support", "tax", "adjustment", "credit", "refund")


@dataclass
class BudgetStatus:
    budget: Budget
    actual: Decimal
    projected_total: Decimal
    pct_of_budget: Decimal
    projected_pct: Decimal
    over_threshold: bool
    over_budget: bool
    days_elapsed: int
    days_total: int


def _q(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


def _aware(dt: datetime) -> datetime:
    """sqlite stores naive; normalize to UTC-aware for comparisons."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _actuals(session: AsyncSession, org_path_prefix: str, b: Budget) -> Decimal:
    stmt = (
        select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0))
        .where(
            CanonicalCostRecord.org_path.like(org_path_prefix + "%"),
            CanonicalCostRecord.line_item_type.in_(SPEND_TYPES),
            CanonicalCostRecord.billing_period_start >= b.period_start,
            CanonicalCostRecord.billing_period_end <= b.period_end,
        )
    )
    if b.customer_id:
        stmt = stmt.where(CanonicalCostRecord.customer_id == b.customer_id)
    if b.account_family_id:
        stmt = stmt.where(CanonicalCostRecord.account_family_id == b.account_family_id)
    if b.cloud_account_id:
        stmt = stmt.where(CanonicalCostRecord.cloud_account_id == b.cloud_account_id)
    if b.provider_code:
        stmt = stmt.where(CanonicalCostRecord.provider_code == b.provider_code)
    return Decimal(str((await session.execute(stmt)).scalar_one() or 0))


def _project(actual: Decimal, b: Budget, now: datetime) -> Decimal:
    """Straight-line burn projection; exact actual for closed/not-started
    periods (no phantom extrapolation)."""
    b_start = _aware(b.period_start)
    b_end = _aware(b.period_end)
    days_total = max((b_end - b_start).days, 1)
    days_elapsed = min(max((now - b_start).days, 0), days_total)
    if days_elapsed <= 0 or b_end <= now or actual == 0:
        return _q(actual)
    daily = actual / days_elapsed
    return _q(actual + daily * (days_total - days_elapsed))


async def budget_status(session: AsyncSession, org_path_prefix: str,
                        b: Budget, now: datetime | None = None) -> BudgetStatus:
    now = _aware(now) if now else datetime.now(UTC)
    actual = await _actuals(session, org_path_prefix, b)
    b_start, b_end = _aware(b.period_start), _aware(b.period_end)
    days_total = max((b_end - b_start).days, 1)
    days_elapsed = min(max((now - b_start).days, 0), days_total)
    projected = _project(actual, b, now)
    amount = Decimal(b.amount or 0)
    pct = _q(actual / amount * 100) if amount else Decimal("0")
    ppct = _q(projected / amount * 100) if amount else Decimal("0")
    threshold = Decimal(b.alert_threshold_pct)
    return BudgetStatus(
        budget=b, actual=_q(actual), projected_total=projected,
        pct_of_budget=pct, projected_pct=ppct,
        over_threshold=ppct >= threshold, over_budget=actual > amount,
        days_elapsed=days_elapsed, days_total=days_total,
    )


async def list_budget_statuses(session: AsyncSession, org_path: str,
                               active_only: bool = True) -> list[BudgetStatus]:
    await set_org_scope(session, org_path)
    now = datetime.now(UTC)
    stmt = select(Budget).where(Budget.org_path.like(org_path + "%"),
                                Budget.deleted_at.is_(None))
    if active_only:
        stmt = stmt.where(Budget.period_end >= now - timedelta(days=45))
    budgets = (await session.execute(stmt.order_by(Budget.period_start.desc()))).scalars().all()
    return [await budget_status(session, org_path, b, now) for b in budgets]


async def org_forecast(session: AsyncSession, org_path: str,
                       months_ahead: int = 3) -> dict:
    """Straight average month-over-month growth over observed canonical
    spend, projected forward. Method-labeled; no confidence is claimed."""
    await set_org_scope(session, org_path)
    rows = (
        await session.execute(
            select(
                CanonicalCostRecord.billing_period_start,
                func.sum(CanonicalCostRecord.provider_billed),
            )
            .where(
                CanonicalCostRecord.org_path.like(org_path + "%"),
                CanonicalCostRecord.line_item_type.in_(SPEND_TYPES),
            )
            .group_by("billing_period_start")
            .order_by("billing_period_start")
        )
    ).all()
    months: dict[str, Decimal] = {}
    for pstart, amt in rows:
        m = str(pstart)[:7]
        months[m] = months.get(m, Decimal("0")) + Decimal(amt or 0)
    ordered = sorted(months)
    if len(ordered) < 2:
        return {"method": "insufficient_history", "months": [], "forecast": []}
    growths = []
    for a, b in zip(ordered, ordered[1:], strict=False):
        if months[a] > 0:
            growths.append(months[b] / months[a] - Decimal("1"))
    avg_growth = (sum(growths, Decimal("0")) / len(growths)) if growths else Decimal("0")
    last = months[ordered[-1]]
    proj = []
    fy, fm = int(ordered[-1][:4]), int(ordered[-1][5:7])
    val = last
    for _ in range(min(months_ahead, 12)):
        fm += 1
        if fm > 12:
            fm, fy = 1, fy + 1
        val = val * (Decimal("1") + avg_growth)
        proj.append({"month": f"{fy:04d}-{fm:02d}", "amount": str(_q(val))})
    return {
        "method": "avg_mom_growth_linear",
        "avg_monthly_growth_pct": str(_q(avg_growth * 100)),
        "months": [{"month": k, "amount": str(_q(months[k]))} for k in ordered],
        "forecast": proj,
    }
