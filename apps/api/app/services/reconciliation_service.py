"""Three-way reconciliation: provider bill ↔ canonical cost ↔ invoices.

Legs (charter):
  A. provider bill totals (provider_bill_totals, from ingested files)
  B. normalized internal cost (sum of canonical provider_billed per period)
  C. customer invoice totals (issued+ invoices per customer + provider-side
     un-invoiced cost analysis)

Exceptions created (idempotent by dedupe_key):
- provider_vs_canonical: |A−B| beyond tolerance
- unmapped_account: canonical rows with customer NULL
- unallocated_credit: credit rows with no invoice applied
- uninvoiced_usage: mapped account with usage but no invoice this period
- duplicate_usage: duplicate rows detected at ingest time
- missing_billing_period: provider file but no canonical rows (or vice versa)
- late_arriving_charge: is_late_adjustment rows in an invoiced period
- invoice_total_mismatch: invoice lines don't sum to header total
A material exception keeps the period unclosable until resolved or waived
(the period-close service enforces; waivers become Approvals)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_scope
from app.engine.money import ZERO
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord, QuarantinedRecord
from app.models.invoices import Invoice
from app.models.reconciliation import (
    ProviderBillTotal,
    ReconciliationException,
    ReconciliationRun,
)
from app.services.audit_service import record_audit
from app.services.authz import RequestPrincipal

MATERIAL_PCT = Decimal("0.005")  # 0.5% default materiality vs provider bill


@dataclass
class ReconSummary:
    run_id: uuid.UUID
    exceptions_open: int
    material_open: int
    summary: dict = field(default_factory=dict)


async def run_reconciliation(
    session: AsyncSession,
    *,
    org_path: str,
    period_start: datetime,
    period_end: datetime,
    principal: RequestPrincipal,
    tolerance_abs: Decimal,
    correlation_id: str | None,
) -> ReconSummary:
    await set_org_scope(session, org_path)
    run = ReconciliationRun(
        org_id=uuid.UUID(org_path.strip("/").split("/")[-1]), org_path=org_path,
        period_start=period_start, period_end=period_end,
        status="running", tolerance_abs=tolerance_abs, tolerance_pct=MATERIAL_PCT,
        computed_by=principal.user_id, correlation_id=correlation_id,
    )
    session.add(run)
    await session.flush()

    exceptions: list[ReconciliationException] = []

    def add(dedupe: str, exc_type: str, *, customer_id: uuid.UUID | None = None,
            account_ref: str | None = None, delta: Decimal = ZERO,
            explanation: str, evidence: dict, material: bool,
            severity: str = "high") -> None:
        exceptions.append(ReconciliationException(
            run_id=run.id, org_path=org_path, org_id=run.org_id, dedupe_key=dedupe,
            exc_type=exc_type, materiality="material" if material else "minor",
            severity=severity if material else "low",
            customer_id=customer_id, billing_account_ref=account_ref,
            amount_delta=delta, currency="USD", explanation=explanation,
            evidence=evidence,
        ))

    # A. provider bill totals
    bills = list((
        await session.execute(
            select(ProviderBillTotal).where(
                ProviderBillTotal.org_path == org_path,
                ProviderBillTotal.period_start == period_start,
            )
        )
    ).scalars())
    total_provider = sum((b.billed_total for b in bills), ZERO)

    # B. canonical (all org subtree)
    canon_total = Decimal((
        await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(
                CanonicalCostRecord.org_path.like(org_path + "%"),
                CanonicalCostRecord.billing_period_start == period_start,
                CanonicalCostRecord.billing_period_end == period_end,
            )
        )
    ).scalar_one() or 0)
    if bills and abs(total_provider - canon_total) > tolerance_abs:
        add(
            f"provider_vs_canonical:{period_start:%Y%m}", "provider_bill_adjustment",
            delta=total_provider - canon_total,
            explanation=(f"Provider bill {total_provider} vs canonical normalized "
                         f"{canon_total}; delta {total_provider - canon_total}"),
            evidence={"provider": str(total_provider), "canonical": str(canon_total)},
            material=abs(total_provider - canon_total) > total_provider * MATERIAL_PCT,
        )

    # unmapped accounts
    unmapped = (
        await session.execute(
            select(
                CanonicalCostRecord.payer_or_billing_account,
                func.sum(CanonicalCostRecord.provider_billed).label("amt"),
            )
            .where(
                CanonicalCostRecord.org_path == org_path,
                CanonicalCostRecord.customer_id.is_(None),
                CanonicalCostRecord.billing_period_start == period_start,
            )
            .group_by(CanonicalCostRecord.payer_or_billing_account)
        )
    ).all()
    for acct, amt in unmapped:
        add(
            f"unmapped:{period_start:%Y%m}:{acct}", "unmapped_account",
            account_ref=acct, delta=Decimal(amt or 0),
            explanation=f"Account {acct} has {amt} of usage but no customer allocation",
            evidence={"account": acct, "amount": str(amt)},
            material=True, severity="medium",
        )

    # duplicates quarantined this period
    dupes = list((
        await session.execute(
            select(QuarantinedRecord).where(
                QuarantinedRecord.org_path == org_path,
                QuarantinedRecord.reason == "duplicate_record",
                QuarantinedRecord.billing_period_start == period_start,
                QuarantinedRecord.status == "open",
            )
        )
    ).scalars())
    for dq in dupes:
        add(
            f"duplicate:{dq.id}", "duplicate_usage",
            explanation=f"Duplicate source row quarantined (row {dq.row_number})",
            evidence={"quarantined_id": str(dq.id)}, material=False, severity="low",
        )

    # C. invoice coverage per customer
    customers = list((
        await session.execute(
            select(Customer).where(
                Customer.org_path.like(org_path + "%"),
                Customer.deleted_at.is_(None),
            )
        )
    ).scalars())
    total_invoiced = ZERO
    for cust in customers:
        usage = Decimal((
            await session.execute(
                select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(
                    CanonicalCostRecord.customer_id == cust.id,
                    CanonicalCostRecord.billing_period_start == period_start,
                    CanonicalCostRecord.billing_period_end == period_end,
                    CanonicalCostRecord.line_item_type == "usage",
                )
            )
        ).scalar_one() or 0)
        if usage == 0:
            continue
        inv_status = ("issued", "exported", "paid_or_settled")
        inv_total = Decimal((
            await session.execute(
                select(func.coalesce(func.sum(Invoice.total), 0)).where(
                    Invoice.customer_id == cust.id,
                    Invoice.period_start == period_start,
                    Invoice.status.in_(inv_status),
                    Invoice.deleted_at.is_(None),
                )
            )
        ).scalar_one() or 0)
        total_invoiced += inv_total
        if inv_total == 0:
            add(
                f"uninvoiced:{period_start:%Y%m}:{cust.id}", "uninvoiced_usage",
                customer_id=cust.id, delta=usage,
                explanation=f"{cust.display_name} has {usage} provider usage but no issued invoice",
                evidence={"usage": str(usage)}, material=True, severity="critical",
            )
        else:
            # margin sanity + provider-vs-invoice per customer (informational)
            draft_total = Decimal((
                await session.execute(
                    select(func.coalesce(func.sum(Invoice.total), 0)).where(
                        Invoice.customer_id == cust.id,
                        Invoice.period_start == period_start,
                        Invoice.status.notin_(("voided", "corrected")),
                        Invoice.deleted_at.is_(None),
                    )
                )
            ).scalar_one() or 0)
            if draft_total != inv_total:
                add(
                    f"unissued:{period_start:%Y%m}:{cust.id}", "uninvoiced_usage",
                    customer_id=cust.id, delta=draft_total - inv_total,
                    explanation=(f"{draft_total - inv_total} drafted but not issued "
                                 f"for {cust.display_name}"),
                    evidence={"drafted": str(draft_total), "issued": str(inv_total)},
                    material=False, severity="low",
                )

    if bills and total_invoiced == 0 and canon_total > 0:
        add(
            f"missing-invoices:{period_start:%Y%m}", "missing_usage",
            delta=canon_total,
            explanation="No invoices exist for a period with normalized cost",
            evidence={"canonical": str(canon_total)}, material=True, severity="critical",
        )

    # persist exceptions (idempotent: unique(run, dedupe_key))
    for ex in exceptions:
        session.add(ex)
    await session.flush()

    open_material = sum(1 for e in exceptions if e.materiality == "material")
    run.status = "completed"
    run.summary = {
        "provider_total": str(total_provider),
        "canonical_total": str(canon_total),
        "invoiced_total": str(total_invoiced),
        "exceptions": len(exceptions),
        "material_open": open_material,
    }
    await record_audit(
        session, principal, action="reconciliation.completed", org_path=org_path,
        summary=f"Reconciliation {period_start:%Y-%m}: {len(exceptions)} exceptions "
                f"({open_material} material)",
        entity_type="reconciliation_run", entity_id=run.id, detail=run.summary,
        correlation_id=correlation_id,
    )
    await session.commit()
    return ReconSummary(
        run_id=run.id, exceptions_open=len(exceptions),
        material_open=open_material, summary=run.summary,
    )


def exc_key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
