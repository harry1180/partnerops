"""Phase 4 service-level tests: anomaly math, budgets, recommendations,
governance. Pure-function coverage first, then sqlite end-to-end passes
against ingested synthetic data (the planted Cobalt 14x spike, the orphan
Azure subscription, missing tags on the legacy account)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.ingestion.synthetic_aws import build_all
from app.ingestion.synthetic_azure import BILLING_PROFILE
from app.ingestion.synthetic_azure import build_all as az_build
from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount, Customer
from app.models.finops import (
    Budget,
    CostAnomaly,
    GovernanceFinding,
    GovernancePolicy,
    PolicyException,
    Recommendation,
)
from app.models.org import Organization
from app.services import anomaly, budgets as bsvc, governance
from app.services import recommendations as rsvc
from app.services.ingest_service import ingest_csv

ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"


# ---------------- pure functions ----------------

def test_robust_z_detects_spike_and_ignores_noise():
    baseline = [Decimal("100")] * 6
    z, med, mad = anomaly.robust_z(Decimal("1400"), baseline, Decimal("100"))
    assert z > 4 and med == 100
    z2, _, _ = anomaly.robust_z(Decimal("110"), baseline, Decimal("100"))
    assert abs(z2) < 1


def test_robust_z_single_prior_spike_does_not_poison():
    prior = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("900"), Decimal("100")]
    z, med, _ = anomaly.robust_z(Decimal("105"), prior, Decimal("100"))
    assert med == 100 and abs(z) < 1


def test_budget_projection_is_straight_line():
    b = Budget(amount=Decimal("1000"), period_start=datetime(2026, 8, 1, tzinfo=UTC),
               period_end=datetime(2026, 8, 31, tzinfo=UTC))
    # 30-day period; 250 spent after 15 days -> projected 500
    s = bsvc._project(Decimal("250"), b, now=datetime(2026, 8, 16, tzinfo=UTC))
    assert s == Decimal("500.00")
    # closed period: no phantom extrapolation
    s2 = bsvc._project(Decimal("250"), b, now=datetime(2026, 9, 15, tzinfo=UTC))
    assert s2 == Decimal("250.00")


def test_normalize_environment():
    from app.services.tagging import normalize_environment
    assert normalize_environment("Prod") == "prod"
    assert normalize_environment("dev") == "nonprod"
    assert normalize_environment("whatever") == "unknown"
    assert normalize_environment(None) == "unknown"


# ---------------- shared workspace: AWS+Azure ingested ----------------

async def _workspace(tag: str):
    """Reseller org with two customers; all three AWS + Azure fixture months
    ingested. 111/222 (AWS) → cust A, 333 (AWS) → cust B; 444/999 (AWS) and
    the orphan Azure sub deliberately unmapped → unallocated spend."""
    org = uuid.uuid4()
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    path_a = f"{org_path}{cust_a}/"
    path_b = f"{org_path}{cust_b}/"
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name=f"P4 {tag}", path=org_path))
        s.add(Organization(id=cust_a, kind="customer", name=f"P4A {tag}", path=path_a))
        s.add(Organization(id=cust_b, kind="customer", name=f"P4B {tag}", path=path_b))
        await s.flush()
        ca = Customer(org_id=org, org_path=path_a, code=f"A{tag[:5]}", display_name=f"A {tag}")
        cb = Customer(org_id=org, org_path=path_b, code=f"B{tag[:5]}", display_name=f"B {tag}")
        s.add_all([ca, cb])
        await s.flush()
        fam_a = AccountFamily(customer_id=ca.id, name="FamA", org_id=org, org_path=path_a)
        fam_b = AccountFamily(customer_id=cb.id, name="FamB", org_id=org, org_path=path_b)
        s.add_all([fam_a, fam_b])
        await s.flush()
        payer = CloudBillingAccount(org_id=org, org_path=org_path, provider_code="aws",
                                    external_id="777700000001", display_name="P", currency="USD")
        ea = CloudBillingAccount(org_id=org, org_path=org_path, provider_code="azure",
                                 external_id=BILLING_PROFILE, display_name="EA", currency="USD")
        s.add_all([payer, ea])
        await s.flush()
        mapping = {"111111111111": (fam_a, path_a, "aws"), "222222222222": (fam_a, path_a, "aws"),
                   "333333333333": (fam_b, path_b, "aws"),
                   # legacy account: mapped but its rows carry NO owner/cost_center
                   "999999999999": (fam_a, path_a, "aws"),
                   "aaaaaaaa-1111-4111-8111-111111111111": (fam_a, path_a, "azure"),
                   "aaaaaaaa-3333-4333-8333-333333333333": (fam_b, path_b, "azure")}
        # note: 444 (AWS) and the orphan azure sub stay absent → unmapped
        # discovery + unallocated spend for the governance policies.
        for ext, (fam, pth, prov) in mapping.items():
            s.add(CloudAccount(provider_code=prov, external_id=ext, display_name=f"{prov} {ext[:6]}",
                               billing_account_id=payer.id if prov == "aws" else ea.id,
                               account_family_id=fam.id, allocation_status="mapped",
                               org_id=org, org_path=pth))
        await s.commit()
    aws = build_all()["777700000001"]
    az = az_build()[BILLING_PROFILE]
    async with SessionLocal() as s:
        for label in ("2026-06", "2026-07", "2026-08"):
            await ingest_csv(s, org_id=org, org_path=org_path, provider_code="aws",
                             parser_version=1, filename=f"{label}.csv",
                             body=aws[label].encode(), object_key=f"t/{tag}{label}",
                             source="synthetic", correlation_id=None, actor_user_id=None)
            await ingest_csv(s, org_id=org, org_path=org_path, provider_code="azure",
                             parser_version=1, filename=f"az-{label}.csv",
                             body=az[label].encode(), object_key=f"t/az{tag}{label}",
                             source="synthetic", correlation_id=None, actor_user_id=None)
    return org, org_path, str(ca.id), str(cb.id)


# ---------------- anomaly pass ----------------

async def _run_anomaly(org_path, now=datetime(2026, 9, 20, tzinfo=UTC)):
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        res = await anomaly.run_anomaly_pass(s, org_path, now=now)
        await s.commit()
    return res


@pytest.mark.asyncio
async def test_anomaly_pass_finds_planted_spike(client, migrated_db):
    _org, org_path, cust_a, _ = await _workspace("anom")
    res = await _run_anomaly(org_path)
    assert res.created >= 1, res
    async with SessionLocal() as s:
        anoms = list((await s.execute(
            select(CostAnomaly).where(CostAnomaly.org_path == org_path)
        )).scalars())
    spike = [a for a in anoms if a.kind == "cost_spike" and "CloudFront" in (a.service or "")]
    assert spike, [(a.kind, a.service) for a in anoms]
    a = spike[0]
    assert a.observed_amount > a.baseline_amount * 5
    assert a.evidence["method"] == "mom_robust_zscore_median_mad"
    assert a.z_score >= Decimal("4.0")
    # relative-floor: a -8% wobble (high z, tiny MAD) must NOT be flagged
    assert not any(a2.kind == "cost_drop" and "CloudFront" in (a2.service or "")
                   for a2 in anoms), [(x.kind, x.service, str(x.z_score)) for x in anoms]

    # idempotent: re-running updates open rows, never duplicates
    res2 = await _run_anomaly(org_path)
    assert res2.created == 0
    assert res2.updated >= 1

    # reviewed states preserved
    async with SessionLocal() as s:
        a0 = (await s.execute(select(CostAnomaly).where(
            CostAnomaly.org_path == org_path, CostAnomaly.status == "open")
            .limit(1))).scalars().first()
        a0.status = "false_positive"
        await s.commit()
    await _run_anomaly(org_path)
    async with SessionLocal() as s:
        still = await s.get(CostAnomaly, a0.id)
        assert still.status == "false_positive"


# ---------------- budgets ----------------

@pytest.mark.asyncio
async def test_budget_status_and_variance(client, migrated_db):
    _org, org_path, cust_a, _ = await _workspace("budg")
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        b = Budget(org_id=uuid.UUID(org_path.strip("/").split("/")[-1]),
                   org_path=org_path, name="Acme cap", scope_kind="customer",
                   customer_id=uuid.UUID(cust_a), amount=Decimal("1000"),
                   period_start=datetime(2026, 6, 1, tzinfo=UTC),
                   period_end=datetime(2026, 7, 1, tzinfo=UTC),
                   alert_threshold_pct=80)
        s.add(b)
        await s.commit()
        bid = b.id
    async with SessionLocal() as s:
        statuses = await bsvc.list_budget_statuses(s, org_path, active_only=False)
        mine = [x for x in statuses if x.budget.id == bid]
    assert len(mine) == 1
    st = mine[0]
    assert st.actual > 0
    assert st.over_budget, f"actual {st.actual} vs 1000 cap"
    # closed period: projected == actual (no phantom burn extrapolation)
    assert st.projected_total == st.actual
    assert st.pct_of_budget > 100
    assert st.over_threshold


@pytest.mark.asyncio
async def test_forecast_labels_its_method(client, migrated_db):
    _org, org_path, _, _ = await _workspace("fc")
    async with SessionLocal() as s:
        f = await bsvc.org_forecast(s, org_path)
    assert f["method"] in ("avg_mom_growth_linear", "insufficient_history")
    if f["method"] == "avg_mom_growth_linear":
        assert len(f["months"]) >= 3 and len(f["forecast"]) == 3
        assert all(Decimal(m["amount"]) > 0 for m in f["months"])


# ---------------- recommendations ----------------

@pytest.mark.asyncio
async def test_recommendation_pass_evidence_and_honest_realization(client, migrated_db):
    _org, org_path, _, _ = await _workspace("rec")
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        res = await rsvc.run_recommendation_pass(s, org_path,
                                                 now=datetime(2026, 9, 20, tzinfo=UTC))
        await s.commit()
    assert res.resources_evaluated > 10
    async with SessionLocal() as s:
        recs = list((await s.execute(
            select(Recommendation).where(Recommendation.org_path == org_path)
        )).scalars())
    assert recs
    assert all(r.basis for r in recs), "every rec must carry an evidence basis"
    assert all(r.confidence in ("low", "medium", "high") for r in recs)
    # advice only: none may claim the platform took action
    assert all("we purchased" not in (r.detail or "").lower() for r in recs)
    assert all(r.title and r.detail for r in recs)

    # accept a rec, then realize savings with NO post-decision data: must
    # honestly realize nothing, never fabricate a number
    async with SessionLocal() as s:
        r0 = next(r for r in recs if r.status == "open")
        r0.status = "accepted"
        r0.decided_at = datetime(2026, 9, 1, tzinfo=UTC)
        await s.commit()
        rid = r0.id
    async with SessionLocal() as s:
        n = await rsvc.realize_savings(s, org_path, now=datetime(2026, 10, 1, tzinfo=UTC))
        await s.commit()
    assert n == 0
    async with SessionLocal() as s:
        r1 = await s.get(Recommendation, rid)
        assert r1.realized_at is None and r1.realized_savings is None


# ---------------- governance ----------------

@pytest.mark.asyncio
async def test_governance_findings_and_exception_lifecycle(client, migrated_db):
    _org, org_path, _, _ = await _workspace("gov")
    root_uuid = uuid.UUID(org_path.strip("/").split("/")[-1])
    async with SessionLocal() as s:
        await set_org_scope(s, org_path)
        s.add_all([
            GovernancePolicy(org_id=root_uuid, org_path=org_path,
                             name="Owner+CC required", kind="required_tags",
                             severity="medium",
                             parameters={"keys": ["owner", "cost_center"]}),
            GovernancePolicy(org_id=root_uuid, org_path=org_path,
                             name="Only east regions", kind="approved_regions",
                             severity="high", parameters={"regions": ["eastus2"]}),
            GovernancePolicy(org_id=root_uuid, org_path=org_path,
                             name="No unallocated spend", kind="unallocated_cost",
                             severity="critical", parameters={}),
        ])
        await s.commit()

    async def _eval(now=None):
        async with SessionLocal() as s:
            await set_org_scope(s, org_path)
            r = await governance.evaluate_policies(s, org_path, now=now)
            await s.commit()
        return r

    res = await _eval()
    assert res.findings_new > 0
    async with SessionLocal() as s:
        findings = list((await s.execute(
            select(GovernanceFinding).where(GovernanceFinding.org_path == org_path)
        )).scalars())
        pols = {p.id: p.kind for p in (await s.execute(
            select(GovernancePolicy).where(
                GovernancePolicy.org_path == org_path))).scalars()}
    kinds = {pols[f.policy_id] for f in findings}
    # unallocated: Cobalt 444 + orphan sub + 999 + enrollment rows (AWS+Azure)
    assert "unallocated_cost" in kinds, kinds
    # AWS us-east-1 outside the eastus2 allow-list
    assert "approved_regions" in kinds, kinds
    # legacy account has no owner/cost-center tags
    assert "required_tags" in kinds, kinds

    # grant exception → excepted survives re-eval
    target = next(f for f in findings if pols[f.policy_id] == "unallocated_cost")
    async with SessionLocal() as s:
        s.add(PolicyException(org_id=root_uuid, org_path=org_path,
                              finding_id=target.id, reason="tracked in ticket GOV-1",
                              expires_at=datetime(2026, 12, 1, tzinfo=UTC)))
        target.status = "excepted"
        await s.commit()
    res2 = await _eval()
    assert res2.findings_excepted >= 1

    # expire it → revoked, finding reopened
    async with SessionLocal() as s:
        exc = (await s.execute(select(PolicyException).where(
            PolicyException.org_path == org_path))).scalars().first()
        exc.expires_at = datetime(2026, 9, 1, tzinfo=UTC)
        await s.commit()
    res3 = await _eval(now=datetime(2026, 9, 20, tzinfo=UTC))
    assert res3.exceptions_expired >= 1
    async with SessionLocal() as s:
        reopened = await s.get(GovernanceFinding, target.id)
        assert reopened.status == "open"

    # idempotent re-eval creates no new rows
    async def _count():
        async with SessionLocal() as s:
            return (await s.execute(select(func.count(GovernanceFinding.id)).where(
                GovernanceFinding.org_path == org_path))).scalar_one()
    before = await _count()
    await _eval()
    assert await _count() == before
