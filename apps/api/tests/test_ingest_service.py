"""Ingestion service integration tests (sqlite, real models).

Covers charter requirements: idempotent re-submit, duplicate detection,
quarantine, unmapped accounts, late adjustments, provider bill totals,
raw-file lineage, Decimal round-trip. Each test gets its own tenant org so
rows can never cross-contaminate (sqlite has no RLS here — that layer is
tested against real PostgreSQL in test_pg_isolation.py)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.ingestion.synthetic_aws import build_all
from app.models.billing_core import (
    AccountFamily,
    CloudAccount,
    CloudBillingAccount,
    Customer,
)
from app.models.cost import (
    CanonicalCostRecord,
    QuarantinedRecord,
    RawBillingFile,
    RawBillingRecord,
)
from app.models.org import Organization
from app.models.reconciliation import ProviderBillTotal
from app.services.ingest_service import ingest_csv

ROOT_PATH = "/11111111-1111-4111-8111-111111111111/"


async def _new_workspace(tag: str) -> tuple[uuid.UUID, str]:
    """Fresh reseller org + customer + family + payer + mapped accounts.

    The 999 account is mapped here as 'mapped' by default; tests that need
    the unmapped scenario delete it explicitly."""
    org = uuid.uuid4()
    cust_org = uuid.uuid4()
    org_path = f"{ROOT_PATH}{org}/"
    cust_path = f"{org_path}{cust_org}/"
    async with SessionLocal() as s:
        await set_org_scope(s, ROOT_PATH)
        s.add(Organization(id=org, kind="reseller", name=f"IW {tag}", path=org_path))
        s.add(Organization(id=cust_org, kind="customer", name=f"IWC {tag}", path=cust_path))
        await s.flush()
        cust = Customer(org_id=org, org_path=cust_path, code=f"IW{tag[:6]}", display_name=f"IW {tag} Co")
        s.add(cust)
        await s.flush()
        fam = AccountFamily(customer_id=cust.id, name="Primary", org_id=org, org_path=cust_path)
        s.add(fam)
        await s.flush()
        payer = CloudBillingAccount(
            org_id=org, org_path=org_path, provider_code="aws",
            external_id="777700000001", display_name="Payer", currency="USD",
        )
        s.add(payer)
        await s.flush()
        for ext in ("111111111111", "222222222222", "333333333333",
                    "444444444444", "999999999999"):
            s.add(CloudAccount(
                provider_code="aws", external_id=ext, display_name=f"AWS {ext}",
                billing_account_id=payer.id, account_family_id=fam.id,
                allocation_status="mapped", org_id=org, org_path=cust_path,
            ))
        await s.commit()
    return org, org_path


async def _ingest(label: str, org: uuid.UUID, org_path: str, fname: str, extra: bytes = b""):
    body = build_all()["777700000001"][label].encode() + b"\n" + extra + b"\n"
    async with SessionLocal() as s:
        return await ingest_csv(
            s, org_id=org, org_path=org_path, provider_code="aws", parser_version=1,
            filename=fname, body=body, object_key=f"t/{fname}", source="synthetic",
            correlation_id=f"c-{fname}", actor_user_id=None,
        )


@pytest.mark.asyncio
async def test_ingest_full_pipeline_and_idempotency(client, migrated_db):
    org, org_path = await _new_workspace("idem")
    first = await _ingest("2026-06", org, org_path, "2026-06.csv", b"# idem")
    assert first.status == "parsed"
    assert first.canonical > 40

    # same bytes again → skipped
    body = build_all()["777700000001"]["2026-06"].encode() + b"\n# idem\n"
    async with SessionLocal() as s:
        second = await ingest_csv(
            s, org_id=org, org_path=org_path, provider_code="aws", parser_version=1,
            filename="2026-06.csv", body=body, object_key="t/2026-06.csv",
            source="synthetic", correlation_id="c2", actor_user_id=None,
        )
    assert second.skipped_duplicate_file

    async with SessionLocal() as s:
        files = (await s.execute(
            select(func.count(RawBillingFile.id)).where(RawBillingFile.org_path == org_path)
        )).scalar_one()
        canon = (await s.execute(
            select(func.count(CanonicalCostRecord.id)).where(
                CanonicalCostRecord.org_path.like(org_path + "%"))
        )).scalar_one()
        raws = (await s.execute(
            select(func.count(RawBillingRecord.id)).where(RawBillingRecord.org_path == org_path)
        )).scalar_one()
    assert files == 1
    assert canon == first.canonical
    assert raws >= canon


@pytest.mark.asyncio
async def test_july_duplicate_row_quarantined(client, migrated_db):
    org, org_path = await _new_workspace("dup")
    res = await _ingest("2026-07", org, org_path, "2026-07.csv", b"# dup")
    assert res.duplicates == 1
    async with SessionLocal() as s:
        dups = (await s.execute(
            select(func.count(QuarantinedRecord.id)).where(
                QuarantinedRecord.reason == "duplicate_record",
                QuarantinedRecord.file_id == res.file_id,
            )
        )).scalar_one()
        credit_rows = (await s.execute(
            select(func.count(CanonicalCostRecord.id)).where(
                CanonicalCostRecord.line_item_type.in_(["credit", "refund"]),
                CanonicalCostRecord.lineage_file_id == res.file_id,
            )
        )).scalar_one()
    assert dups == 1
    assert credit_rows >= 2


@pytest.mark.asyncio
async def test_august_late_adjustment_and_provider_total(client, migrated_db):
    org, org_path = await _new_workspace("aug")
    res = await _ingest("2026-08", org, org_path, "2026-08.csv", b"# aug")
    async with SessionLocal() as s:
        late = list((await s.execute(
            select(CanonicalCostRecord).where(
                CanonicalCostRecord.is_late_adjustment.is_(True),
                CanonicalCostRecord.lineage_file_id == res.file_id,
            )
        )).scalars())
        totals = list((await s.execute(
            select(ProviderBillTotal).where(ProviderBillTotal.org_path == org_path)
        )).scalars())
    assert len(late) == 1
    assert late[0].line_item_type == "adjustment"
    # leg-1 invoice-level total is exactly one row; Phase 3 also records
    # per-account rollups (level='account') which must not pollute leg A.
    invoice_level = [t for t in totals if t.level == "invoice"]
    account_level = [t for t in totals if t.level == "account"]
    assert len(invoice_level) == 1
    assert len(account_level) >= 5  # 4 linked AWS accounts (+enrollment only in azure grain)
    assert "?" not in {t.billing_account_ref for t in account_level}  # AWS rows always mapped
    # provider summary row made leg-1 exact: line-sum + 4500.00
    usage_sum = Decimal((await _usage_sum(res.file_id, org_path)) or 0)
    expected = Decimal(str(invoice_level[0].billed_total))
    assert expected - usage_sum == Decimal("4500.000000")


async def _usage_sum(file_id: uuid.UUID, org_path: str) -> str:
    async with SessionLocal() as s:
        val = (await s.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(
                CanonicalCostRecord.lineage_file_id == file_id,
            )
        )).scalar_one()
    return str(val)


@pytest.mark.asyncio
async def test_unmapped_account_rows_have_no_customer(client, migrated_db):
    org, org_path = await _new_workspace("unmap")
    async with SessionLocal() as s:
        acct = (await s.execute(
            select(CloudAccount).where(
                CloudAccount.external_id == "999999999999",
                CloudAccount.org_path == f"{org_path}",
            ).limit(1)
        )).scalars().first()
        # accounts were seeded at the customer path; find this org's copy
        if acct is None:
            acct = (await s.execute(
                select(CloudAccount).where(
                    CloudAccount.external_id == "999999999999",
                    CloudAccount.org_path.like(org_path + "%"),
                ).limit(1)
            )).scalars().first()
        assert acct is not None
        await s.delete(acct)
        await s.commit()
    res = await _ingest("2026-06", org, org_path, "2026-06-u.csv", b"# unmapped")
    assert "999999999999" in res.unmapped_accounts
    async with SessionLocal() as s:
        null_cust = (await s.execute(
            select(func.count(CanonicalCostRecord.id)).where(
                CanonicalCostRecord.customer_id.is_(None),
                CanonicalCostRecord.lineage_file_id == res.file_id,
            )
        )).scalar_one()
    assert null_cust > 0


@pytest.mark.asyncio
async def test_money_columns_round_trip_decimal(client, migrated_db):
    org, org_path = await _new_workspace("money")
    res = await _ingest("2026-06", org, org_path, "2026-06-m.csv", b"# money")
    async with SessionLocal() as s:
        rows = list((await s.execute(
            select(CanonicalCostRecord).where(CanonicalCostRecord.lineage_file_id == res.file_id)
            .limit(10)
        )).scalars())
    assert rows
    for r in rows:
        assert isinstance(r.provider_billed, Decimal)
        assert r.provider_billed == r.provider_billed.quantize(Decimal("0.000001"))
