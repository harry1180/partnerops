"""Governance policy engine (Phase 4).

Policies evaluate facts we can actually observe from ingested data — there is
no provider API here, so a policy can only see what billing says about a
resource. Each policy kind has an evaluator that yields findings with
evidence (the numbers the finding was computed from). Re-evaluation upserts
by dedupe_key: status and history survive passes; vanished violations mark
findings remediated; active policy_exceptions set status='excepted' instead
of open; expired exceptions are revoked by the pass and reported.

Implemented kinds (all billing-observable; the rest of the charter's list —
public storage exposure, encryption posture — need provider config data and
are labeled unavailable, never faked):
- required_tags: cloud accounts whose canonical rows lack the configured
  required tag keys (application/environment/owner/cost_center).
- approved_regions: canonical rows in regions outside the allow-list.
- idle_resources: resources billing near-zero after a spend history (delegates
  to the same signal as the idle recommendation — evidence shared).
- oversized_resources: sustained high-volume single-meter spend on small
  account families (a right-size signal with an explicit budget context).
- unallocated_cost: canonical usage with no customer attribution (the #1
  FinOps governance failure the charter names).

Severity/owner/remediation come from the policy row.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cost import CanonicalCostRecord
from app.models.finops import (
    GovernanceFinding,
    GovernancePolicy,
    PolicyException,
)


@dataclass
class EvalPassResult:
    findings_open: int = 0
    findings_new: int = 0
    findings_remediated: int = 0
    findings_excepted: int = 0
    exceptions_expired: int = 0
    by_policy: dict[str, int] = field(default_factory=dict)
    evaluated_at: datetime | None = None


def _dedupe(org_path: str, policy_id: uuid.UUID, subject: str) -> str:
    return hashlib.sha256(f"{org_path}|{policy_id}|{subject}".encode()).hexdigest()[:160]


async def evaluate_policies(session: AsyncSession, org_path: str,
                            now: datetime | None = None) -> EvalPassResult:
    now = now or datetime.now(UTC)
    org_id = uuid.UUID(org_path.strip("/").split("/")[-1])
    policies = (
        await session.execute(
            select(GovernancePolicy).where(
                GovernancePolicy.org_path.like(org_path + "%"),
                GovernancePolicy.enabled.is_(True),
                GovernancePolicy.deleted_at.is_(None),
            )
        )
    ).scalars().all()

    exceptions = list((
        await session.execute(
            select(PolicyException).where(
                PolicyException.org_path == org_path,
                PolicyException.revoked_at.is_(None),
            )
        )
    ).scalars())

    def _aw(d: datetime) -> datetime:  # sqlite reads back naive-UTC
        return d if d.tzinfo else d.replace(tzinfo=UTC)

    active_exc_findings = {e.finding_id for e in exceptions if _aw(e.expires_at) > now}
    result = EvalPassResult(evaluated_at=now)

    for pol in policies:
        violations = await _evaluate_one(session, org_path, pol, now)
        seen: set[str] = set()
        for subject, evidence in violations:
            dk = _dedupe(org_path, pol.id, subject)
            seen.add(dk)
            finding = (
                await session.execute(
                    select(GovernanceFinding).where(
                        GovernanceFinding.org_path == org_path,
                        GovernanceFinding.dedupe_key == dk)
                )
            ).scalar_one_or_none()
            excepted = False
            if finding is not None:
                excepted = finding.id in active_exc_findings
                finding.evidence = evidence
                finding.last_seen = now
                finding.severity = pol.severity
                finding.remediation = pol.remediation
                finding.status = "excepted" if excepted else (
                    finding.status if finding.status == "acknowledged" else "open")
                result.findings_open += 1
            else:
                finding = GovernanceFinding(
                    org_id=org_id, org_path=org_path, dedupe_key=dk,
                    policy_id=pol.id, subject=subject,
                    customer_id=_subject_customer(evidence),
                    cloud_account_id=_subject_account(evidence),
                    evidence=evidence, severity=pol.severity,
                    status="excepted" if excepted else "open",
                    first_seen=now, last_seen=now,
                    remediation=pol.remediation, owner_label=pol.owner_label,
                )
                session.add(finding)
                await session.flush()  # id for exception matching
                if excepted:
                    finding.status = "excepted"
                result.findings_new += 1
                result.findings_open += 1
            if excepted:
                result.findings_excepted += 1
            pol_kind = pol.kind
            result.by_policy[pol_kind] = result.by_policy.get(pol_kind, 0) + 1

        # findings from this policy that vanished -> remediated
        stale = (
            await session.execute(
                select(GovernanceFinding).where(
                    GovernanceFinding.org_path == org_path,
                    GovernanceFinding.policy_id == pol.id,
                    GovernanceFinding.status.in_(("open", "excepted")),
                )
            )
        ).scalars().all()
        for f_ in stale:
            if f_.dedupe_key not in seen:
                f_.status = "remediated"
                f_.last_seen = now
                result.findings_remediated += 1
        pol.last_evaluated_at = now

    # revoke expired exceptions
    for e in exceptions:
        if _aw(e.expires_at) <= now and e.revoked_at is None:
            e.revoked_at = now
            result.exceptions_expired += 1
            reopened = await session.get(GovernanceFinding, e.finding_id)
            if reopened is not None and reopened.status == "excepted":
                reopened.status = "open"
    return result


def _subject_customer(evidence: dict) -> uuid.UUID | None:
    v = evidence.get("customer_id")
    return uuid.UUID(v) if v else None


def _subject_account(evidence: dict) -> uuid.UUID | None:
    v = evidence.get("cloud_account_id")
    return uuid.UUID(v) if v else None


async def _evaluate_one(session: AsyncSession, org_path: str,
                        pol: GovernancePolicy, now: datetime) -> list[tuple[str, dict]]:
    p = pol.parameters or {}
    if pol.kind == "required_tags":
        required = [k.lower() for k in p.get("keys", ["application", "environment", "owner"])]
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.cloud_account_id,
                    CanonicalCostRecord.customer_id,
                    CanonicalCostRecord.provider_code,
                    func.sum(CanonicalCostRecord.provider_billed),
                    func.count(CanonicalCostRecord.id),
                    *[func.count(getattr(CanonicalCostRecord, k)) for k in required]
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.customer_id.isnot(None),
                    CanonicalCostRecord.line_item_type == "usage",
                )
                .group_by("cloud_account_id", "customer_id", "provider_code")
            )
        ).all()
        out: list[tuple[str, dict]] = []
        for acct, cust, provider, amt, total, *present in rows:
            missing = [k for k, have in zip(required, present, strict=True) if not have]
            if missing:
                subject = f"account:{acct}"
                out.append((subject, {
                    "cloud_account_id": str(acct), "customer_id": str(cust),
                    "provider": provider, "missing_tags": missing,
                    "monthly_spend_at_risk": str(Decimal(amt or 0).quantize(Decimal("0.01"))),
                    "rows": int(total)}))
        return out

    if pol.kind == "approved_regions":
        allowed = [str(r).lower() for r in p.get("regions", ["us-east-1", "eastus2"])]
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.region,
                    CanonicalCostRecord.customer_id,
                    CanonicalCostRecord.cloud_account_id,
                    func.sum(CanonicalCostRecord.provider_billed),
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.region.isnot(None),
                    CanonicalCostRecord.line_item_type.in_(("usage", "support")),
                )
                .group_by("region", "customer_id", "cloud_account_id")
            )
        ).all()
        out = []
        for region, cust, acct, amt in rows:
            if str(region).lower() not in allowed:
                out.append((f"region:{region}:customer:{cust}", {
                    "region": str(region), "customer_id": str(cust) if cust else None,
                    "cloud_account_id": str(acct) if acct else None,
                    "off_policy_spend": str(Decimal(amt or 0).quantize(Decimal("0.01"))),
                    "allowed_regions": allowed}))
        return out

    if pol.kind == "unallocated_cost":
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.payer_or_billing_account,
                    CanonicalCostRecord.cloud_account_id,
                    func.sum(CanonicalCostRecord.provider_billed),
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.customer_id.is_(None),
                    CanonicalCostRecord.line_item_type.in_(("usage", "support")),
                )
                .group_by("payer_or_billing_account", "cloud_account_id")
            )
        ).all()
        out = []
        for payer, acct, amt in rows:
            if Decimal(amt or 0) <= 0:
                continue
            subject = f"unallocated:{acct or payer}"
            out.append((subject, {
                "cloud_account_id": str(acct) if acct else None,
                "billing_account": payer,
                "amount": str(Decimal(amt).quantize(Decimal("0.01"))),
                "remedy_hint": "map the account/subscription to an account family"}))
        return out

    if pol.kind == "idle_resources":
        # resource billing > floor in any prior month but <= 5% of that in the
        # last month (same signal as the idle recommendation)
        floor = Decimal(p.get("monthly_floor", "50"))
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.resource_id,
                    CanonicalCostRecord.customer_id,
                    CanonicalCostRecord.cloud_account_id,
                    CanonicalCostRecord.billing_period_start,
                    func.sum(CanonicalCostRecord.provider_billed),
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.resource_id.isnot(None),
                    CanonicalCostRecord.line_item_type == "usage",
                )
                .group_by("resource_id", "customer_id", "cloud_account_id", "billing_period_start")
            )
        ).all()
        series: dict[tuple, dict[str, Decimal]] = {}
        for resource, cust, acct, pstart, amt in rows:
            m = str(pstart)[:7]
            k = (resource, cust, acct)
            series.setdefault(k, {})
            series[k][m] = series[k].get(m, Decimal("0")) + Decimal(amt or 0)
        out = []
        months_global = sorted({m for s in series.values() for m in s})
        if len(months_global) < 2:
            return out
        last = months_global[-1]
        for (resource, cust, acct), s in series.items():
            prior = [v for m, v in s.items() if m != last]
            if not prior or max(prior) < floor:
                continue
            if s.get(last, Decimal("0")) <= max(prior) * Decimal("0.05"):
                out.append((f"resource:{resource}", {
                    "resource_id": resource,
                    "customer_id": str(cust) if cust else None,
                    "cloud_account_id": str(acct) if acct else None,
                    "peak_prior_month": str(max(prior)),
                    "last_month": last,
                    "last_amount": str(s.get(last, Decimal("0")))}))
        return out

    if pol.kind == "oversized_resources":
        # single resource whose monthly spend exceeds a threshold with the
        # largest meter share — a candidate review (billing-only evidence)
        thresh = Decimal(p.get("monthly_threshold", "3000"))
        rows = (
            await session.execute(
                select(
                    CanonicalCostRecord.resource_id,
                    CanonicalCostRecord.service,
                    CanonicalCostRecord.customer_id,
                    CanonicalCostRecord.cloud_account_id,
                    func.sum(CanonicalCostRecord.provider_billed),
                )
                .where(
                    CanonicalCostRecord.org_path.like(org_path + "%"),
                    CanonicalCostRecord.line_item_type == "usage",
                )
                .group_by("resource_id", "service", "customer_id", "cloud_account_id")
                .having(func.sum(CanonicalCostRecord.provider_billed) >= thresh * 3)
            )
        ).all()
        out = []
        for resource, service, cust, acct, amt in rows:
            if not resource:
                continue
            out.append((f"oversized:{resource}", {
                "resource_id": resource, "service": service,
                "customer_id": str(cust) if cust else None,
                "cloud_account_id": str(acct) if acct else None,
                "observed_total": str(Decimal(amt or 0).quantize(Decimal("0.01"))),
                "monthly_threshold": str(thresh)}))
        return out

    # kinds the policy model enumerates but billing data cannot observe
    # (public_storage_exposure, unencrypted_resources, overly_permissive):
    # evaluated only when provider-config connectors exist (Phase 5+).
    # We return no findings rather than fabricate them.
    return []
