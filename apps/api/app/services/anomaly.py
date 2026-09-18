"""Anomaly detection pass (Phase 4).

Method (honest per charter: "understandable statistical methods and
configurable thresholds" — no ML is claimed or used):

Robust month-over-month z-score. For each subject (customer × service)
bucket provider-billed spend per billing month. For the latest month with
data, compute median and MAD of the PRIOR months (excluding the candidate),
then

    robust_z = 0.6745 * (observed - median) / max(MAD, mad_floor)

Flag when |robust_z| >= threshold (default 4.0) AND |observed - median| >=
abs_floor (default $500) AND the relative change is material
(>= rel_floor_pct of baseline, default 50%). Median/MAD are robust to a
single prior spike; the floors stop tiny-variance series from screaming —
with only 2 prior months a MAD of ~$1k makes a -8% wobble score z=-6, which
is mathematically true but operationally noise, so a relative floor gates
it. Requires >= 2 prior months — history-starved series are skipped rather
than guessed at.

The demo/fixture data is monthly-grain (one row per account/service/month),
so this is the grain that carries statistical meaning end-to-end; when real
CUR daily feeds land, a daily-grain variant is additive config, not a
rewrite. The method string records the grain honestly.

Outputs are idempotent per (org, subject, month, kind) via dedupe_key;
re-runs refresh evidence on open rows and never overwrite reviewed states.
"""

from __future__ import annotations

import hashlib
import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import CanonicalCostRecord
from app.models.finops import CostAnomaly

ROBUST_SCALE = Decimal("0.6745")  # 1/1.4826: MAD -> sigma equivalent
MIN_PRIOR_MONTHS = 2


@dataclass
class AnomalyPassResult:
    created: int = 0
    updated: int = 0
    subjects: int = 0


def robust_z(observed: Decimal, prior: list[Decimal],
             mad_floor: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """(z, median, mad) of observed vs a robust prior baseline. Pure
    function — unit-tested directly."""
    if not prior:
        return Decimal("0"), Decimal("0"), Decimal("0")
    med = Decimal(str(statistics.median(prior)))
    mad = Decimal(str(statistics.median([abs(x - med) for x in prior])))
    denom = max(mad, mad_floor)
    z = ROBUST_SCALE * (observed - med) / denom
    return z, med, mad


def _month_of(dt: datetime | str) -> str:
    if isinstance(dt, str):
        return dt[:7]
    return dt.astimezone(UTC).strftime("%Y-%m")


async def run_anomaly_pass(
    session: AsyncSession,
    org_path: str,
    *,
    threshold_z: Decimal = Decimal("4.0"),
    abs_floor: Decimal = Decimal("500"),
    mad_floor: Decimal = Decimal("100"),
    rel_floor_pct: Decimal = Decimal("50"),
    now: datetime | None = None,
) -> AnomalyPassResult:
    """Detect month-over-month spend anomalies over canonical rows in this
    org subtree. Writes cost_anomalies; reviewed states are preserved."""
    now = now or datetime.now(UTC)
    org_id = uuid.UUID(org_path.strip("/").split("/")[-1])
    rows = (
        await session.execute(
            select(
                CanonicalCostRecord.customer_id,
                CanonicalCostRecord.cloud_account_id,
                CanonicalCostRecord.service,
                CanonicalCostRecord.billing_period_start,
                func.sum(CanonicalCostRecord.provider_billed),
            )
            .where(
                CanonicalCostRecord.org_path.like(org_path + "%"),
                CanonicalCostRecord.line_item_type.in_(("usage", "support", "tax", "adjustment")),
            )
            .group_by("customer_id", "cloud_account_id", "service", "billing_period_start")
        )
    ).all()

    series: dict[tuple, dict[str, Decimal]] = {}
    for cust, acct, service, pstart, amt in rows:
        if pstart is None:
            continue
        m = _month_of(pstart)
        key = (cust, acct, service)
        buckets = series.setdefault(key, {})
        buckets[m] = buckets.get(m, Decimal("0")) + Decimal(amt or 0)

    result = AnomalyPassResult()
    fired_keys: set[str] = set()
    for (cust, acct, service), buckets in series.items():
        result.subjects += 1
        months = sorted(m for m in buckets if m <= _month_of(now))
        if len(months) < MIN_PRIOR_MONTHS + 1:
            continue
        candidate = months[-1]
        prior = [buckets[m] for m in months[:-1]][-12:]  # trailing 12 months
        if len(prior) < MIN_PRIOR_MONTHS:
            continue
        obs = buckets[candidate]
        z, med, mad = robust_z(obs, prior, mad_floor)
        delta = obs - med
        rel_ok = med > 0 and abs(delta) >= med * rel_floor_pct / 100
        if not (abs(z) >= threshold_z and abs(delta) >= abs_floor and rel_ok):
            continue
        kind = "cost_spike" if delta > 0 else "cost_drop"
        dk = hashlib.sha256(
            f"{org_path}|{cust}|{acct}|{service}|{candidate}|{kind}".encode()).hexdigest()[:128]
        fired_keys.add(dk)
        evidence = {
            "method": "mom_robust_zscore_median_mad",
            "month": candidate, "observed": str(obs), "baseline_median": str(med),
            "baseline_mad": str(mad), "prior_months": len(prior),
            "threshold_z": str(threshold_z), "abs_floor": str(abs_floor),
        }
        detected = datetime.fromisoformat(f"{candidate}-28T23:59:59+00:00")
        existing = (
            await session.execute(
                select(CostAnomaly).where(CostAnomaly.org_path == org_path,
                                          CostAnomaly.dedupe_key == dk)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(CostAnomaly(
                org_id=org_id, org_path=org_path, dedupe_key=dk, kind=kind,
                customer_id=cust, cloud_account_id=acct, service=service,
                detected_on=detected, observed_amount=obs, baseline_amount=med,
                z_score=z.quantize(Decimal("0.0001")), threshold_used=threshold_z,
                window_days=30, method="mom_robust_zscore_median_mad",
                evidence=evidence,
            ))
            result.created += 1
        elif existing.status == "open":  # leave acknowledged/false_positive alone
            existing.observed_amount = obs
            existing.baseline_amount = med
            existing.z_score = z.quantize(Decimal("0.0001"))
            existing.evidence = evidence
            result.updated += 1

    # config change can un-fire a previously open anomaly: resolve it rather
    # than leave stale warnings (reviewed states are still preserved)
    stale = (
        await session.execute(
            select(CostAnomaly).where(
                CostAnomaly.org_path == org_path,
                CostAnomaly.status == "open",
            )
        )
    ).scalars().all()
    for a in stale:
        if a.dedupe_key not in fired_keys:
            a.status = "resolved"
            a.review_note = "auto-resolved: no longer exceeds thresholds on re-run"
    return result
