"""Phase 3: Azure adapter + multi-cloud ingestion tests.

Unit level (no DB): parsing, field mapping, quarantine, enrollment-scope
handling, and a hypothesis fuzz asserting the parser never crashes on
malformed CSV and never silently drops a row (every row → draft or issue).

Service level (migrated sqlite): the Azure fixture ingests, discovers the
orphan subscription as an unmapped CloudAccount, and records per-account
bill totals alongside the invoice-level row with the planted +180.00
true-up.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hsettings
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.ingestion.adapters import SyntheticAzureAdapter
from app.ingestion.base import IngestContext
from app.ingestion.synthetic_azure import BILLING_PROFILE, PERIODS, build_all

AZURE_HEADER = [
    "billing_period_start", "billing_period_end", "usage_start", "usage_end",
    "line_item_id", "invoice_id", "line_item_type", "cost_category",
    "billing_profile_id", "billing_account_name", "subscription_id",
    "subscription_name", "resource_group", "resource_id", "service", "meter",
    "meter_region", "quantity", "unit", "currency", "unit_price",
    "list_cost", "ondemand_cost_equivalent", "pre_tax_cost", "amortized_cost",
    "effective_cost", "net_cost", "credit_cost", "tax_cost", "support_fee_cost",
    "marketplace_cost", "reservation_id", "tag_application", "tag_environment",
    "tag_owner", "tag_cost_center", "is_late_adjustment",
]


def ctx(label: str) -> IngestContext:
    start, end, lbl = next(p for p in PERIODS if p[2] == label)
    return IngestContext(
        org_id="00000000-0000-4000-8000-000000000000",
        org_path="/",
        provider_code="azure",
        parser_version=1,
        billing_period_start=start,
        billing_period_end=end,
    )


@pytest.fixture(scope="module")
def months():
    return build_all()[BILLING_PROFILE]


def test_azure_fixture_is_deterministic():
    a = build_all()
    b = build_all()
    assert a == b, "same seed must produce byte-identical fixtures"


def test_azure_adapter_parses_all_months(months):
    a = SyntheticAzureAdapter()
    for label, text in months.items():
        res = a.parse(text, ctx(label))
        assert res.drafts, label
        assert res.row_count == len(res.drafts) + len(res.issues)


def test_field_mapping(months):
    res = SyntheticAzureAdapter().parse(months["2026-06"], ctx("2026-06"))
    usage = [d for d in res.drafts if d.line_item_type == "usage"]
    assert usage
    d = usage[0]
    assert isinstance(d.provider_billed, Decimal)
    assert d.payer_account == BILLING_PROFILE
    assert d.linked_account and d.linked_account != BILLING_PROFILE  # subscription
    assert d.source_metadata["format"] == "synthetic-azure-csv-v1"
    assert d.source_metadata["resource_group"]
    assert d.region == "eastus2"
    assert d.application and d.cost_center  # tags mapped


def test_reservation_lines_billed_below_ondemand(months):
    res = SyntheticAzureAdapter().parse(months["2026-06"], ctx("2026-06"))
    covered = [d for d in res.drafts if d.source_metadata.get("reservation_id")]
    assert covered
    for d in covered:
        assert d.provider_billed < d.ondemand_equivalent


def test_july_credit_refund_and_duplicate(months):
    res = SyntheticAzureAdapter().parse(months["2026-07"], ctx("2026-07"))
    types = {d.line_item_type for d in res.drafts}
    assert "credit" in types and "refund" in types
    keys = [d.dedupe_key for d in res.drafts]
    assert len(keys) - len(set(keys)) == 1  # planted duplicate row


def test_august_late_adjustment_and_summary(months):
    res = SyntheticAzureAdapter().parse(months["2026-08"], ctx("2026-08"))
    late = [d for d in res.drafts if d.usage_start < ctx("2026-08").billing_period_start]
    assert len(late) == 1 and late[0].line_item_type == "adjustment"
    summaries = [d for d in res.drafts if d.line_item_type == "provider_summary"]
    assert len(summaries) == 1
    line_sum = sum((d.provider_billed for d in res.drafts
                    if d.line_item_type != "provider_summary"), Decimal("0"))
    assert summaries[0].provider_billed - line_sum == Decimal("180.000000")


def test_enrollment_scope_rows_have_no_subscription(months):
    """Tax/credit rows carry subscription_id == billing_profile: they parse
    with linked_account=None (no fake discovery of the EA itself)."""
    res = SyntheticAzureAdapter().parse(months["2026-06"], ctx("2026-06"))
    enroll = [d for d in res.drafts if d.line_item_type == "tax"]
    assert enroll, "fixture must include enrollment-level tax rows"
    for d in enroll:
        assert d.linked_account is None
        assert d.payer_account == BILLING_PROFILE


def test_quarantine_on_bad_currency():
    def row(**over):
        base = {
            "billing_period_start": "2026-06-01T00:00:00+00:00",
            "billing_period_end": "2026-07-01T00:00:00+00:00",
            "usage_start": "2026-06-01T00:00:00+00:00",
            "usage_end": "2026-06-15T00:00:00+00:00",
            "line_item_id": "q-1", "invoice_id": "INV", "line_item_type": "usage",
            "cost_category": "Compute", "billing_profile_id": BILLING_PROFILE,
            "billing_account_name": "ea", "subscription_id": "sub-1",
            "subscription_name": "s", "resource_group": "rg", "resource_id": "/r",
            "service": "Virtual Machines", "meter": "B2ms", "meter_region": "eastus2",
            "quantity": "10", "unit": "Hrs", "currency": "USD", "unit_price": "0.083000",
            "list_cost": "830", "ondemand_cost_equivalent": "830", "pre_tax_cost": "830",
            "amortized_cost": "830", "effective_cost": "830", "net_cost": "830",
            "credit_cost": "0", "tax_cost": "0", "support_fee_cost": "0",
            "marketplace_cost": "0", "reservation_id": "", "tag_application": "",
            "tag_environment": "", "tag_owner": "", "tag_cost_center": "",
            "is_late_adjustment": "false",
        }
        base.update(over)
        return [base[k] for k in AZURE_HEADER]
    res = SyntheticAzureAdapter().parse(
        _csv([row(currency="ZZZ"), row(line_item_id="q-2", marketplace_cost="")]), ctx("2026-06"))
    # bad-currency row quarantined; valid row with empty numeric parses
    assert len(res.issues) == 1 and res.issues[0].reason == "invalid_currency"
    assert len(res.drafts) == 1
    assert res.drafts[0].marketplace_fee == Decimal("0")


# ---------------- hypothesis: format fuzz ----------------
# The adapter is the blast shield between a malformed provider export and
# the canonical model. Rule: it may reject rows (issues), but must never
# crash and must never drop rows silently.

_cell = st.text(max_size=18).filter(lambda s: "\n" not in s and "\r" not in s)


def _csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    for r in rows:
        buf.write(",".join('"' + c.replace('"', '""') + '"' for c in r) + "\n")
    return f"{','.join(AZURE_HEADER)}\n{buf.getvalue()}"


@hsettings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    n=st.integers(0, 4),
    cells=st.lists(_cell, min_size=148, max_size=148),
    bad_currency=st.booleans(),
)
def test_fuzz_never_crashes_and_no_silent_drops(n, cells, bad_currency):
    rows = []
    for r in range(n):
        vals = [cells[(r * 37 + i) % len(cells)] for i in range(37)]
        # pin the fields the adapter inspects for STRUCTURAL decisions; the
        # fuzz explores the remaining value space (amounts/dates/garbage)
        vals[0] = "2026-06-01T00:00:00+00:00"
        vals[1] = "2026-07-01T00:00:00+00:00"
        vals[4] = f"fuzz-{r}"          # line_item_id present
        vals[8] = BILLING_PROFILE      # billing_profile_id
        vals[10] = "sub-fuzz-1"        # subscription_id (not enrollment-scoped)
        vals[17] = "10"                # quantity
        vals[19] = "XXX" if bad_currency else "USD"
        vals[36] = "false"             # is_late_adjustment
        rows.append(vals)
    res = SyntheticAzureAdapter().parse(_csv(rows), ctx("2026-06"))
    assert res.row_count == n
    assert len(res.drafts) + len(res.issues) == n  # accounting: nothing vanishes
    for d in res.drafts:
        assert isinstance(d.provider_billed, Decimal)
        assert d.currency == "USD"
    if bad_currency and n:
        assert not res.drafts
        assert all(i.reason == "invalid_currency" for i in res.issues)


@hsettings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(amount=st.decimals(min_value=-10000, max_value=10000, places=6,
                          allow_nan=False, allow_infinity=False))
def test_fuzz_amounts_survive_roundtrip(amount):
    base = {
        "billing_period_start": "2026-06-01T00:00:00+00:00",
        "billing_period_end": "2026-07-01T00:00:00+00:00",
        "usage_start": "2026-06-01T00:00:00+00:00",
        "usage_end": "2026-06-15T00:00:00+00:00",
        "line_item_id": "amt-1", "invoice_id": "INV", "line_item_type": "usage",
        "cost_category": "Compute", "billing_profile_id": BILLING_PROFILE,
        "billing_account_name": "ea", "subscription_id": "sub-1",
        "subscription_name": "s", "resource_group": "rg", "resource_id": "/r",
        "service": "Virtual Machines", "meter": "B2ms", "meter_region": "eastus2",
        "quantity": "10", "unit": "Hrs", "currency": "USD", "unit_price": "0.083000",
        "list_cost": str(amount), "ondemand_cost_equivalent": "0",
        "pre_tax_cost": str(amount), "amortized_cost": "0", "effective_cost": "0",
        "net_cost": "0", "credit_cost": "0", "tax_cost": "0", "support_fee_cost": "0",
        "marketplace_cost": "0", "reservation_id": "", "tag_application": "",
        "tag_environment": "", "tag_owner": "", "tag_cost_center": "",
        "is_late_adjustment": "false",
    }
    res = SyntheticAzureAdapter().parse(_csv([[base[k] for k in AZURE_HEADER]]), ctx("2026-06"))
    assert len(res.drafts) == 1, res.issues
    assert res.drafts[0].provider_billed == amount


# ---------------- service level: multi-cloud ingest ----------------

ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"

AZURE_MAPPED = ("aaaaaaaa-1111-4111-8111-111111111111",
                "aaaaaaaa-2222-4222-8222-222222222222",
                "aaaaaaaa-3333-4333-8333-333333333333")


async def _new_azure_workspace(tag: str) -> tuple[uuid.UUID, str]:
    """Fresh reseller org + customer + family + EA billing account + three
    mapped subscriptions. The orphan sub is deliberately absent → discovery."""
    org = uuid.uuid4()
    cust_org = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    cust_path = f"{org_path}{cust_org}/"
    from app.core.db import SessionLocal
    from app.db.rls import set_org_scope
    from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount, Customer
    from app.models.org import Organization

    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name=f"AW {tag}", path=org_path))
        s.add(Organization(id=cust_org, kind="customer", name=f"AWC {tag}", path=cust_path))
        await s.flush()
        cust = Customer(org_id=org, org_path=cust_path, code=f"AW{tag[:6]}",
                        display_name=f"AW {tag} Co")
        s.add(cust)
        await s.flush()
        fam = AccountFamily(customer_id=cust.id, name="Primary", org_id=org, org_path=cust_path)
        s.add(fam)
        await s.flush()
        ea = CloudBillingAccount(
            org_id=org, org_path=org_path, provider_code="azure",
            external_id=BILLING_PROFILE, display_name="EA", currency="USD",
        )
        s.add(ea)
        await s.flush()
        for ext in AZURE_MAPPED:
            s.add(CloudAccount(
                provider_code="azure", external_id=ext, display_name=f"sub {ext[:8]}",
                billing_account_id=ea.id, account_family_id=fam.id,
                allocation_status="mapped", org_id=org, org_path=cust_path,
            ))
        await s.commit()
    return org, org_path


@pytest.mark.asyncio
async def test_azure_ingest_multicloud(client, migrated_db):
    from app.core.db import SessionLocal
    from app.models.billing_core import CloudAccount
    from app.models.cost import CanonicalCostRecord
    from app.models.reconciliation import ProviderBillTotal
    from app.services.ingest_service import ingest_csv

    org, org_path = await _new_azure_workspace("p3")
    body = build_all()[BILLING_PROFILE]["2026-08"].encode()
    async with SessionLocal() as s:
        summary = await ingest_csv(
            s, org_id=org, org_path=org_path, provider_code="azure", parser_version=1,
            filename="2026-08.csv", body=body, object_key="t/az-2026-08.csv",
            source="synthetic", correlation_id="c-az", actor_user_id=None,
        )
    assert summary.status == "parsed"
    assert summary.canonical >= 30  # 31 rows incl. one provider_summary leg
    assert summary.duplicates == 0  # duplicate was planted in July, not Aug
    # orphan subscription surfaced via auto-discovery, unmapped; enrollment
    # rows are partner-scope, not "unmapped" noise
    assert any(a.startswith("unmapped-") for a in summary.unmapped_accounts)
    assert "?" not in summary.unmapped_accounts

    async with SessionLocal() as s:
        orphan = (await s.execute(
            select(CloudAccount).where(
                CloudAccount.external_id == "unmapped-0000-4000-8000-000000000099",
                CloudAccount.org_path.like(org_path + "%"))
        )).scalar_one()
        assert orphan.provider_code == "azure"
        assert orphan.allocation_status == "unmapped"

        canon = list((await s.execute(
            select(CanonicalCostRecord).where(
                CanonicalCostRecord.lineage_file_id == summary.file_id)
        )).scalars())
        azure_rows = [r for r in canon if r.provider_code == "azure"]
        assert len(azure_rows) == summary.canonical
        # mapped rows carry the customer; enrollment rows have no account
        mapped = [r for r in azure_rows if r.customer_id is not None]
        assert mapped and all(r.cloud_account_id for r in mapped)
        assert any(r.cloud_account_id is None for r in azure_rows)

        totals = list((await s.execute(
            select(ProviderBillTotal).where(ProviderBillTotal.org_path == org_path)
        )).scalars())
        inv = [t for t in totals if t.level == "invoice"]
        acct = [t for t in totals if t.level == "account"]
        assert len(inv) == 1 and len(acct) >= 3
        usage_sum = sum((r.provider_billed for r in azure_rows
                         if r.line_item_type != "provider_summary"), Decimal("0"))
        assert inv[0].billed_total - usage_sum == Decimal("180.000000")
        # per-account rollups exist for each mapped subscription
        for ext in AZURE_MAPPED:
            row = [t for t in acct if t.billing_account_ref == ext]
            assert row, ext
            assert row[0].billed_total != 0


@pytest.mark.asyncio
async def test_azure_ingest_idempotent(client, migrated_db):
    from app.core.db import SessionLocal
    from app.services.ingest_service import ingest_csv

    org, org_path = await _new_azure_workspace("p3idem")
    body = build_all()[BILLING_PROFILE]["2026-06"].encode()
    async with SessionLocal() as s:
        first = await ingest_csv(
            s, org_id=org, org_path=org_path, provider_code="azure", parser_version=1,
            filename="2026-06.csv", body=body, object_key="t/az-idem.csv",
            source="synthetic", correlation_id="c-a1", actor_user_id=None,
        )
    async with SessionLocal() as s:
        second = await ingest_csv(
            s, org_id=org, org_path=org_path, provider_code="azure", parser_version=1,
            filename="2026-06.csv", body=body, object_key="t/az-idem.csv",
            source="synthetic", correlation_id="c-a2", actor_user_id=None,
        )
    assert not first.skipped_duplicate_file
    assert second.skipped_duplicate_file


@pytest.mark.asyncio
async def test_aws_and_azure_coexist_per_customer(client, migrated_db):
    """Multi-cloud: the SAME customer (workspace) carries AWS accounts and an
    Azure subscription; canonical rows keep providers distinct and pricing
    aggregates across both by customer."""
    from app.core.db import SessionLocal
    from app.db.rls import set_org_scope
    from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount, Customer
    from app.models.cost import CanonicalCostRecord
    from app.models.org import Organization
    from app.services.ingest_service import ingest_csv

    org = uuid.uuid4()
    cust_org = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    cust_path = f"{org_path}{cust_org}/"
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name="MC Org", path=org_path))
        s.add(Organization(id=cust_org, kind="customer", name="MC Cust", path=cust_path))
        await s.flush()
        cust = Customer(org_id=org, org_path=cust_path, code="MCCO", display_name="MultiCloud Co")
        s.add(cust)
        await s.flush()
        fam = AccountFamily(customer_id=cust.id, name="All", org_id=org, org_path=cust_path)
        s.add(fam)
        await s.flush()
        aws_p = CloudBillingAccount(org_id=org, org_path=org_path, provider_code="aws",
                                    external_id="777700000001", display_name="P", currency="USD")
        az_p = CloudBillingAccount(org_id=org, org_path=org_path, provider_code="azure",
                                   external_id=BILLING_PROFILE, display_name="EA", currency="USD")
        s.add_all([aws_p, az_p])
        await s.flush()
        s.add_all([
            CloudAccount(provider_code="aws", external_id="111111111111", display_name="a",
                         billing_account_id=aws_p.id, account_family_id=fam.id,
                         allocation_status="mapped", org_id=org, org_path=cust_path),
            CloudAccount(provider_code="azure", external_id=AZURE_MAPPED[0], display_name="b",
                         billing_account_id=az_p.id, account_family_id=fam.id,
                         allocation_status="mapped", org_id=org, org_path=cust_path),
        ])
        await s.commit()

    from app.ingestion.synthetic_aws import build_all as aws_build
    async with SessionLocal() as s:
        await ingest_csv(s, org_id=org, org_path=org_path, provider_code="aws",
                         parser_version=1, filename="june-aws.csv",
                         body=aws_build()["777700000001"]["2026-06"].encode(),
                         object_key="t/mc-aws", source="synthetic",
                         correlation_id="mc1", actor_user_id=None)
    async with SessionLocal() as s:
        await ingest_csv(s, org_id=org, org_path=org_path, provider_code="azure",
                         parser_version=1, filename="june-az.csv",
                         body=build_all()[BILLING_PROFILE]["2026-06"].encode(),
                         object_key="t/mc-az", source="synthetic",
                         correlation_id="mc2", actor_user_id=None)
    async with SessionLocal() as s:
        by_provider = dict((await s.execute(
            select(CanonicalCostRecord.provider_code, func.count(CanonicalCostRecord.id))
            .where(CanonicalCostRecord.customer_id == cust.id)
            .group_by(CanonicalCostRecord.provider_code)
        )).all())
    assert by_provider.get("aws") and by_provider.get("azure"), by_provider
