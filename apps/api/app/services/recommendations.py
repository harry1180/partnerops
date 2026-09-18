"""Optimization recommendations (Phase 4).

All signals derive from canonical cost rows — no provider API calls, no
telemetry claims. Each recommendation carries `basis` (the exact numbers it
computed from) so the UI shows evidence, and `confidence` honestly labels
inference strength. The platform never acts on provider resources: accept/
dismiss is a tracked decision, and realized savings are MEASURED from later
spend, never projected.

Kinds (all billing-observable):
- idle_resource: a resource billed above a floor in prior months but <=5% of
  that in the latest month — the forgotten-leftover pattern.
- rightsizing: non-production environment at >=40% of same-service production
  spend (volume heuristic; confidence medium).
- commitment_gap: covered-category (Compute/Database) spend with no
  reservation applied in any month — estimated from the discount factor
  actually observed on covered spend in the same book of business.
- marketplace_review: an ISV line billing an identical amount every month —
  license-utilization review (savings not estimated; unknown = honest 0).

Savings realization: for accepted recommendations, average monthly spend of
the same resource/service BEFORE the decision month vs AFTER it. Only writes
when post-decision data exists; the recorded basis makes the number
contestable. No post data → no realized number, ever.
"""

from __future__ import annotations

import hashlib
import statistics
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import CanonicalCostRecord
from app.models.finops import Recommendation

CENT = Decimal("0.01")
COMPUTE_TOKENS = ("Compute", "Database", "EC2", "RDS", "PostgreSQL")
MONTH_SLACK = timedelta(days=120)


@dataclass
class RecPassResult:
    created: int = 0
    refreshed: int = 0
    resources_evaluated: int = 0


def _quant(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


async def run_recommendation_pass(session: AsyncSession, org_path: str,
                                  now: datetime | None = None) -> RecPassResult:
    """(Re)compute recommendations over canonical usage rows in this org
    subtree. Upsert by dedupe_key: open recs refresh with new evidence;
    decided recs are never touched; open recs for resources that stopped
    billing entirely are auto-dismissed with a note (audit trail keeps it)."""
    now = now or datetime.now(UTC)
    org_id = uuid.UUID(org_path.strip("/").split("/")[-1])
    result = RecPassResult()

    rows = (
        await session.execute(
            select(
                CanonicalCostRecord.customer_id,
                CanonicalCostRecord.cloud_account_id,
                CanonicalCostRecord.resource_id,
                CanonicalCostRecord.service,
                CanonicalCostRecord.sku,
                CanonicalCostRecord.region,
                CanonicalCostRecord.environment,
                CanonicalCostRecord.billing_period_start,
                func.sum(CanonicalCostRecord.provider_billed),
                func.sum(CanonicalCostRecord.ondemand_equivalent),
                func.sum(CanonicalCostRecord.credit),
            )
            .where(
                CanonicalCostRecord.org_path.like(org_path + "%"),
                CanonicalCostRecord.line_item_type == "usage",
                CanonicalCostRecord.resource_id.isnot(None),
            )
            .group_by("customer_id", "cloud_account_id", "resource_id", "service", "sku",
                      "region", "environment", "billing_period_start")
            .order_by("resource_id", "billing_period_start")
        )
    ).all()

    # key -> month -> (billed, ondemand)
    res: dict[tuple, dict[str, Decimal]] = {}
    ondemand_by: dict[tuple, dict[str, Decimal]] = {}
    meta: dict[tuple, dict] = {}
    for (cust, acct, resource, service, sku, region, env, pstart,
         billed, ondemand, _credit) in rows:
        if pstart is None or not resource:
            continue
        key = (cust, acct, resource, service, sku)
        m = str(pstart)[:7]
        res.setdefault(key, {})
        ondemand_by.setdefault(key, {})
        res[key][m] = res[key].get(m, Decimal("0")) + Decimal(billed or 0)
        ondemand_by[key][m] = ondemand_by[key].get(m, Decimal("0")) + Decimal(ondemand or 0)
        meta.setdefault(key, {"region": region, "env": (env or "").lower()})
        result.resources_evaluated += 1

    # reservation coverage: a covered-category resource is "covered" if ANY
    # row of it in ANY month recorded a reservation id in source metadata.
    # We can't see that post-grouping, so detect coverage from the amounts:
    # billed < ondemand-equivalent ⇒ discount was applied (RI/SP/marketing).
    covered = {k for k, months in ondemand_by.items()
               if any(months[m] > res[k][m] + Decimal("0.01") for m in months)}
    # observed discount factor on covered compute-ish spend
    factors: list[Decimal] = [
        (sum(res[k].values(), Decimal("0")) / sum(ondemand_by[k].values(), Decimal("0")))
        for k in covered
        if any(t in (k[3] or "") for t in COMPUTE_TOKENS)
        and sum(ondemand_by[k].values(), Decimal("0")) > 0
    ]
    covered_discount: Decimal | None = None
    if factors:
        covered_discount = _quant(Decimal(str(statistics.median(factors))))

    all_months = sorted({m for s in res.values() for m in s})
    latest_month = all_months[-1] if all_months else None
    seen_keys: set[str] = set()

    async def emit(kind: str, key: tuple, title: str, detail: str, remediation: str,
                   saving: Decimal, confidence: str, basis: dict) -> None:
        dk = hashlib.sha256(
            f"{org_path}|{kind}|{key[0]}|{key[2]}|{key[3]}".encode()).hexdigest()[:128]
        seen_keys.add(dk)
        cust, acct, resource, service = key[0], key[1], key[2], key[3]
        existing = (
            await session.execute(
                select(Recommendation).where(Recommendation.org_path == org_path,
                                             Recommendation.dedupe_key == dk)
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.status == "open":
                existing.title, existing.detail, existing.remediation = title, detail, remediation
                existing.estimated_monthly_saving = _quant(saving)
                existing.basis = basis
                existing.confidence = confidence
                result.refreshed += 1
            return
        session.add(Recommendation(
            org_id=org_id, org_path=org_path, dedupe_key=dk, kind=kind,
            customer_id=cust, cloud_account_id=acct, resource_id=resource,
            service=service, region=meta.get(key, {}).get("region"),
            title=title, detail=detail, remediation=remediation,
            estimated_monthly_saving=_quant(saving), confidence=confidence,
            basis=basis,
        ))
        result.created += 1

    if latest_month is None:
        return result

    for key, series in res.items():
        cust, acct, resource, service, sku = key
        months = sorted(series)
        if len(months) < 2:
            continue
        last_m = months[-1]
        last_amt = series[last_m]
        prior_amts = [series[m] for m in months[:-1]]
        prior_avg = (sum(prior_amts, Decimal("0")) / len(prior_amts)) \
            if prior_amts else Decimal("0")
        env = meta.get(key, {}).get("env") or ""

        # idle_resource
        if prior_avg >= Decimal("50") and last_amt <= prior_avg * Decimal("0.05"):
            await emit("idle_resource", key,
                       f"Idle leftover spend on {service} resource",
                       f"Resource {resource} averaged {_quant(prior_avg)}/month across "
                       f"{len(prior_amts)} month(s) but billed only {_quant(last_amt)} in "
                       f"{last_m}; residual charges typically mean forgotten attached "
                       "storage, snapshots or IPs.",
                       "Verify the resource is intended to be decommissioned and remove "
                       "anything still billing against it.",
                       saving=last_amt if last_amt > 0 else Decimal("0"),
                       confidence="high",
                       basis={"prior_avg": str(_quant(prior_avg)), "last_month": last_m,
                              "last_amount": str(_quant(last_amt)),
                              "prior_months": len(prior_amts)})

        # rightsizing (volume heuristic, honestly labeled medium confidence)
        if env in ("dev", "test", "nonprod", "orphan", "sandbox", "qa") \
                and prior_avg >= Decimal("200"):
            prod_keys = [k for k in res
                         if k[3] == service and meta.get(k, {}).get("env") == "prod"]
            prod_last = sum((res[k].get(last_m, Decimal("0")) for k in prod_keys),
                            Decimal("0"))
            if prod_last and last_amt >= prod_last * Decimal("0.4"):
                savings = last_amt - prod_last * Decimal("0.25")
                await emit("rightsizing", key,
                           f"Non-production {service} at near-production scale",
                           f"{resource} (env={env}) billed {_quant(last_amt)} in {last_m} "
                           f"vs {_quant(prod_last)} across production {service} — "
                           "non-prod this large is a classic overprovisioning signal.",
                           "Schedule non-production compute to off-hours and right-size "
                           "instances to observed usage.",
                           saving=max(savings, Decimal("0")),
                           confidence="medium",
                           basis={"last_month": last_m,
                                  "nonprod_amount": str(_quant(last_amt)),
                                  "prod_amount": str(_quant(prod_last)),
                                  "ratio": str((last_amt / prod_last).quantize(
                                      Decimal("0.01"))) if prod_last else None})

        # commitment_gap: never-discounted covered-category spend
        if key not in covered and covered_discount is not None \
                and any(t in (service or "") for t in COMPUTE_TOKENS) \
                and prior_avg >= Decimal("300"):
            savings = prior_avg * (Decimal("1") - covered_discount)
            await emit("commitment_gap", key,
                       f"Uncovered {service} spend could take a commitment",
                       f"{resource} billed {_quant(prior_avg)}/month with no reservation "
                       f"or savings-plan discount applied; comparable covered spend in "
                       f"this book of business ran at {covered_discount * 100:.0f}% of "
                       "on-demand equivalent.",
                       "Propose a commitment at the observed coverage level to an "
                       "authorized buyer. The platform never purchases commitments.",
                       saving=savings, confidence="medium",
                       basis={"prior_avg": str(_quant(prior_avg)),
                              "observed_discount": str(covered_discount)})

        # marketplace_review: perfectly flat ISV lines
        if "marketplace" in (service or "").lower() and len(months) >= 2 \
                and prior_avg >= Decimal("100"):
            flat = all(abs(series[m] - prior_avg) <= Decimal("1.00") for m in months)
            if flat:
                await emit("marketplace_review", key,
                           f"Review recurring marketplace license {sku or ''}".strip(),
                           f"{service} line {resource} billed an identical "
                           f"{_quant(prior_avg)}/month across all observed months — "
                           "verify seats are still in use.",
                           "Confirm license usage with the team; cancel or resize at renewal.",
                           saving=Decimal("0"), confidence="low",
                           basis={"months": len(months), "monthly": str(_quant(prior_avg))})

    # auto-dismiss open recs whose resource stopped appearing entirely
    recent_cut = now - MONTH_SLACK
    stale = (
        await session.execute(
            select(Recommendation).where(
                Recommendation.org_path == org_path,
                Recommendation.status == "open",
                Recommendation.resource_id.isnot(None),
            )
        )
    ).scalars().all()
    for rec in stale:
        if rec.dedupe_key in seen_keys or not rec.resource_id:
            continue
        still_bills = (
            await session.execute(
                select(func.count(CanonicalCostRecord.id)).where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.resource_id == rec.resource_id,
                    CanonicalCostRecord.billing_period_start >= recent_cut)
            )
        ).scalar_one()
        if still_bills == 0:
            rec.status = "dismissed"
            rec.decision_note = "auto-dismissed: resource no longer appears in recent billing data"
            rec.decided_at = now
    return result


async def realize_savings(session: AsyncSession, org_path: str,
                          now: datetime | None = None) -> int:
    """Measured savings for ACCEPTED recommendations: the resource's average
    monthly billed spend BEFORE decision month vs AFTER it. Requires actual
    post-decision rows; otherwise realizes nothing (never projects)."""
    now = now or datetime.now(UTC)
    accepted = (
        await session.execute(
            select(Recommendation).where(
                Recommendation.org_path.like(org_path + "%"),
                Recommendation.status == "accepted",
                Recommendation.decided_at.isnot(None),
                Recommendation.realized_at.is_(None),
            )
        )
    ).scalars().all()
    count = 0
    for rec in accepted:
        if not (rec.resource_id and rec.decided_at):
            continue
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.billing_period_start,
                    func.sum(CanonicalCostRecord.provider_billed),
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.resource_id == rec.resource_id,
                    CanonicalCostRecord.line_item_type == "usage",
                )
                .group_by("billing_period_start")
                .order_by("billing_period_start")
            )
        ).all()
        decided_m = rec.decided_at.strftime("%Y-%m")
        before = [(str(p)[:7], Decimal(a or 0)) for p, a in rows if str(p)[:7] < decided_m]
        after = [(str(p)[:7], Decimal(a or 0)) for p, a in rows if str(p)[:7] > decided_m]
        if not before or not after:
            continue  # no post-decision month yet — wait, never project
        before_avg = sum((a for _, a in before), Decimal("0")) / len(before)
        after_avg = sum((a for _, a in after), Decimal("0")) / len(after)
        delta = before_avg - after_avg
        if delta > 0:
            rec.realized_savings = _quant(delta)
            rec.realized_at = now
            rec.realized_basis = {
                "method": "measured_monthly_spend_before_vs_after",
                "before_avg": str(_quant(before_avg)), "before_months": len(before),
                "after_avg": str(_quant(after_avg)), "after_months": len(after),
                "decision_month": decided_m,
            }
            count += 1
    return count
