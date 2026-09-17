"""Ingestion service: file bytes → raw rows → canonical records.

Transaction model (restartable):
1. unique (sha256, parser_version) file row — re-submission of identical
   bytes is a no-op (idempotency)
2. pure adapter parse (never touches the DB)
3. raw rows stored for lineage; invalid rows → quarantine
4. account allocation: linked_account → CloudAccount → family/customer.
   Unknown accounts keep rows with NULL customer (unmapped → DQ dashboard)
5. provider bill total (leg 1 of reconciliation) upserted from line sums
6. canonical rows append-only: reprocessing after correction = new file,
   new rows; nothing is ever UPDATEd or deleted here.
"""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.rls import set_org_scope
from app.ingestion.adapters import SyntheticAwsAdapter
from app.ingestion.base import IngestContext
from app.models.audit import AuditEvent
from app.models.billing_core import AccountFamily, CloudAccount, CloudBillingAccount
from app.models.cost import (
    CanonicalCostRecord,
    QuarantinedRecord,
    RawBillingFile,
    RawBillingRecord,
)
from app.models.reconciliation import ProviderBillTotal

log = get_logger(__name__)

# provider_code, parser_version → adapter factory
ADAPTERS: dict[tuple[str, int], type] = {("aws", 1): SyntheticAwsAdapter}


@dataclass
class IngestSummary:
    file_id: uuid.UUID
    status: str
    rows: int
    canonical: int
    duplicates: int
    quarantined: int
    unmapped_accounts: list[str]
    skipped_duplicate_file: bool = False


def _now() -> datetime:
    return datetime.now(UTC)


def _scan_period(body: bytes) -> tuple[datetime, datetime]:
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    starts: list[datetime] = []
    ends: list[datetime] = []
    for row in reader:
        try:
            starts.append(datetime.fromisoformat(row["billing_period_start"]))
            ends.append(datetime.fromisoformat(row["billing_period_end"]))
        except (KeyError, ValueError):
            continue
    if not starts:
        raise ValueError("file contains no parseable billing_period_start values")
    # A file's billing period is the LATEST period it names: late-arriving
    # adjustment rows carry an older usage window but belong to this period.
    return max(starts), max(ends)


async def _account_index(session: AsyncSession, org_path: str) -> dict[str, CloudAccount]:
    rows = (
        await session.execute(
            select(CloudAccount).where(CloudAccount.org_path.like(org_path + "%"))
        )
    ).scalars()
    return {a.external_id: a for a in rows}


async def _billing_account_for(
    session: AsyncSession, org_path: str, payer_account: str | None
) -> CloudBillingAccount | None:
    if not payer_account:
        return None
    return (
        await session.execute(
            select(CloudBillingAccount).where(
                CloudBillingAccount.org_path == org_path,
                CloudBillingAccount.external_id == payer_account,
            )
        )
    ).scalar_one_or_none()


async def ingest_csv(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    org_path: str,
    provider_code: str,
    parser_version: int,
    filename: str,
    body: bytes,
    object_key: str,
    source: str,
    correlation_id: str | None,
    actor_user_id: uuid.UUID | None,
) -> IngestSummary:
    key = (provider_code, parser_version)
    if key not in ADAPTERS:
        raise ValueError(f"no adapter for {key}")
    adapter = ADAPTERS[key]()
    sha = hashlib.sha256(body).hexdigest()

    await set_org_scope(session, org_path)

    existing = (
        await session.execute(
            select(RawBillingFile).where(
                RawBillingFile.sha256 == sha,
                RawBillingFile.parser_version == parser_version,
                RawBillingFile.org_path == org_path,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "parsed":
        log.info("ingest_duplicate_file_skipped", file_id=str(existing.id), sha=sha[:12])
        return IngestSummary(
            file_id=existing.id, status="duplicate", rows=existing.row_count,
            canonical=0, duplicates=0, quarantined=0, unmapped_accounts=[],
            skipped_duplicate_file=True,
        )

    period_start, period_end = _scan_period(body)
    ctx = IngestContext(
        org_id=str(org_id), org_path=org_path, provider_code=provider_code,
        parser_version=parser_version,
        billing_period_start=period_start, billing_period_end=period_end,
        correlation_id=correlation_id,
    )

    file = existing or RawBillingFile(
        org_id=org_id, org_path=org_path, provider_code=provider_code, source=source,
        object_key=object_key, original_filename=filename, sha256=sha,
        size_bytes=len(body), parser_version=parser_version, correlation_id=correlation_id,
        ingested_by=actor_user_id,
    )
    file.billing_period_start = period_start
    file.billing_period_end = period_end
    file.status = "parsing"
    file.error_message = None
    session.add(file)
    await session.flush()

    try:
        result = adapter.parse(body.decode("utf-8-sig"), ctx)
    except Exception as exc:
        file.status = "failed"
        file.error_message = str(exc)[:2000]
        file.completed_at = _now()
        await session.commit()
        raise

    accounts = await _account_index(session, org_path)

    # pre-resolve customer per family
    family_customer: dict[uuid.UUID, uuid.UUID] = {}
    for existing_acct in accounts.values():
        if existing_acct.account_family_id and existing_acct.account_family_id not in family_customer:
            fam = await session.get(AccountFamily, existing_acct.account_family_id)
            if fam:
                family_customer[fam.id] = fam.customer_id

    billing_ref = _payer_from(result.drafts)
    billing_account = await _billing_account_for(session, org_path, billing_ref)

    seen_dedupe: set[str] = set()
    duplicates = 0
    unmapped: set[str] = set()
    written = 0
    totals = {
        "billed": Decimal("0"), "credit": Decimal("0"), "tax": Decimal("0"),
        "support": Decimal("0"), "marketplace": Decimal("0"),
    }

    provider_summary_total: Decimal | None = None
    for d in result.drafts:
        session.add(RawBillingRecord(
            file_id=file.id, org_path=org_path, provider_code=provider_code,
            row_number=d.row_number, source_record_id=d.source_record_id,
            dedupe_key=d.dedupe_key, payload=d.source_metadata,
        ))
        if d.line_item_type == "provider_summary":
            # authoritative invoice total from the provider (reconciliation leg
            # 1). Kept as raw row for lineage; never becomes a canonical cost.
            provider_summary_total = d.provider_billed
            continue
        totals["billed"] += d.provider_billed
        totals["credit"] += d.credit
        totals["tax"] += d.tax
        totals["support"] += d.support_fee
        totals["marketplace"] += d.marketplace_fee

        if d.dedupe_key in seen_dedupe:
            duplicates += 1
            session.add(QuarantinedRecord(
                file_id=file.id, org_path=org_path, provider_code=provider_code,
                row_number=d.row_number, billing_period_start=period_start,
                reason="duplicate_record",
                message="duplicate semantic identity within file",
                payload=d.source_metadata,
            ))
            continue
        seen_dedupe.add(d.dedupe_key)

        acct = accounts.get(d.linked_account or "")
        if acct is None and d.linked_account:
            # auto-discovery: accounts seen in provider billing become
            # unmapped CloudAccount rows so they surface on the allocation
            # worklist instead of vanishing into the data-quality page.
            acct = CloudAccount(
                provider_code=provider_code, external_id=d.linked_account,
                display_name=f"Discovered {d.linked_account}",
                billing_account_id=billing_account.id if billing_account else None,
                allocation_status="unmapped",
                account_kind="payer" if d.linked_account == d.payer_account else "member",
                org_id=billing_account.org_id if billing_account else org_id,
                org_path=org_path,
            )
            session.add(acct)
            await session.flush()  # materialize id for canonical rows
            accounts[d.linked_account] = acct
        if acct is None:
            unmapped.add(d.linked_account or "?")
            customer_id = None
            family_id = None
            row_org_path = org_path
        else:
            family_id = acct.account_family_id
            customer_id = family_customer.get(family_id) if family_id else None
            row_org_path = acct.org_path
            if family_id is None and d.linked_account:
                unmapped.add(d.linked_account)  # discovered or explicitly unmapped

        session.add(CanonicalCostRecord(
            lineage_file_id=file.id, source_record_id=d.source_record_id,
            org_path=row_org_path, provider_code=provider_code,
            billing_period_start=period_start, billing_period_end=period_end,
            usage_start=d.usage_start, usage_end=d.usage_end,
            invoice_id=d.invoice_id, payer_or_billing_account=d.payer_account,
            billing_account_id=billing_account.id if billing_account else None,
            cloud_account_id=acct.id if acct else None,
            account_family_id=family_id, customer_id=customer_id,
            resource_id=d.resource_id, service=d.service, sku=d.sku,
            usage_type=d.usage_type, operation=d.operation, region=d.region,
            availability_zone=d.availability_zone, quantity=d.quantity, unit=d.unit,
            currency=d.currency, line_item_type=d.line_item_type,
            cost_category=d.cost_category,
            list_cost=d.list_cost, ondemand_equivalent=d.ondemand_equivalent,
            provider_billed=d.provider_billed, amortized=d.amortized,
            effective=d.effective, net=d.net, credit=d.credit, tax=d.tax,
            support_fee=d.support_fee, marketplace_fee=d.marketplace_fee,
            tags=d.tags, application=d.application, environment=d.environment,
            owner=d.owner, cost_center=d.cost_center,
            source_metadata=d.source_metadata,
            is_late_adjustment=d.usage_start < period_start,
        ))
        written += 1

    for issue in result.issues:
        session.add(QuarantinedRecord(
            file_id=file.id, org_path=org_path, provider_code=provider_code,
            row_number=issue.row_number, billing_period_start=period_start,
            reason=issue.reason, message=issue.message,
            payload={"raw": issue.message},
        ))

    if billing_ref:
        existing_total = (
            await session.execute(
                select(ProviderBillTotal).where(
                    ProviderBillTotal.org_path == org_path,
                    ProviderBillTotal.provider_code == provider_code,
                    ProviderBillTotal.billing_account_ref == billing_ref,
                    ProviderBillTotal.period_start == period_start,
                )
            )
        ).scalar_one_or_none()
        if existing_total is None:
            existing_total = ProviderBillTotal(
                org_path=org_path, provider_code=provider_code,
                billing_account_ref=billing_ref,
                period_start=period_start, period_end=period_end, currency="USD",
            )
            session.add(existing_total)
        existing_total.billed_total = (
            provider_summary_total if provider_summary_total is not None else totals["billed"]
        )
        existing_total.credit_total = totals["credit"]
        existing_total.tax_total = totals["tax"]
        existing_total.support_total = totals["support"]
        existing_total.marketplace_total = totals["marketplace"]
        existing_total.evidence = {"file_id": str(file.id), "sha256": sha}

    file.row_count = len(result.drafts) + len(result.issues)
    file.status = "parsed"
    file.completed_at = _now()
    session.add(AuditEvent(
        actor_user_id=actor_user_id, actor_kind="job", actor_label="ingestion",
        org_path=org_path, action="ingestion.file_parsed",
        entity_type="raw_billing_file", entity_id=file.id,
        summary=(f"{filename}: {file.row_count} rows, {written} canonical, "
                 f"{duplicates} duplicates, {len(result.issues)} quarantined, "
                 f"{len(unmapped)} unmapped accounts"),
        detail={"duplicates": duplicates, "quarantined": len(result.issues),
                "unmapped": sorted(unmapped)},
        correlation_id=correlation_id,
    ))
    await session.commit()

    return IngestSummary(
        file_id=file.id, status=file.status, rows=file.row_count,
        canonical=written, duplicates=duplicates, quarantined=len(result.issues),
        unmapped_accounts=sorted(unmapped),
    )


def _payer_from(drafts) -> str | None:  # type: ignore[no-untyped-def]
    for d in drafts:
        if d.payer_account:
            return d.payer_account
    return None

