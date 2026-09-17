"""Reporting service (Phase 2): on-demand CSV reports + export jobs.

Reports are deterministic aggregations over the same tables the dashboards
use — a report and its dashboard must never disagree. PDF report rendering
and spreadsheet formats are scheduled with Phase 5 integrations; CSV is the
guaranteed format today.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CSRF, Principal, SessionDep
from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.models.billing_core import Customer
from app.models.cost import CanonicalCostRecord
from app.models.invoices import ExportJob, Invoice
from app.services.audit_service import record_audit

router = APIRouter()

REPORTS = {
    "customer_cost_statement": "Cost by customer and period (provider billed)",
    "invoice_summary": "Invoices in period with totals and status",
    "margin_by_customer": "Revenue, provider cost, margin per customer",
    "revenue_leakage": "Unbilled usage + unmapped accounts",
    "contract_expiration": "Active contract versions ending within 60 days",
    "tag_compliance": "Usage rows missing application/owner/cost-center",
}


class ReportRequest(BaseModel):
    period_start: datetime | None = None
    period_end: datetime | None = None


async def _rows(report: str, session, principal: Principal, root: str,
                body: ReportRequest) -> tuple[list[str], list[list[str]]]:
    cw: list = [CanonicalCostRecord.org_path.like(root + "%")]
    if body.period_start:
        cw.append(CanonicalCostRecord.billing_period_start == body.period_start)

    if report == "customer_cost_statement":
        rows = (await session.execute(
            select(Customer.display_name, Customer.code,
                   CanonicalCostRecord.billing_period_start,
                   func.sum(CanonicalCostRecord.provider_billed))
            .join(Customer, Customer.id == CanonicalCostRecord.customer_id)
            .where(*cw, CanonicalCostRecord.line_item_type == "usage")
            .group_by(Customer.display_name, Customer.code,
                      CanonicalCostRecord.billing_period_start)
            .order_by(Customer.display_name))).all()
        return (["customer", "code", "period", "provider_billed"],
                [[a, b, c.isoformat()[:10], str(q(d or ZERO))] for a, b, c, d in rows])

    if report == "invoice_summary":
        iw: list = [Invoice.org_path.like(root + "%"), Invoice.deleted_at.is_(None)]
        if body.period_start:
            iw.append(Invoice.period_start == body.period_start)
        rows = (await session.execute(
            select(Invoice, Customer.display_name)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(*iw).order_by(Invoice.period_start.desc(), Invoice.invoice_number))).all()
        return (["invoice_number", "customer", "period_start", "status", "currency", "total"],
                [[i.invoice_number, cn, i.period_start.isoformat()[:10], i.status,
                  i.currency, str(i.total)] for i, cn in rows])

    if report == "margin_by_customer":
        # same source as /margins/summary service — computed here to keep the
        # report self-contained and deterministic
        from app.api.v1.margins import _summary

        items = await _summary(session, principal, body.period_start)
        return (["customer", "code", "provider_cost", "revenue", "margin", "margin_pct"],
                [[x["customer"], x["code"], x["provider_cost"], x["revenue"],
                  x["margin"], x["margin_pct"] or ""] for x in items])

    if report == "revenue_leakage":
        out: list[list[str]] = []
        unmapped = (await session.execute(
            select(CanonicalCostRecord.payer_or_billing_account,
                   func.sum(CanonicalCostRecord.provider_billed))
            .where(*cw, CanonicalCostRecord.customer_id.is_(None),
                   CanonicalCostRecord.line_item_type == "usage")
            .group_by(CanonicalCostRecord.payer_or_billing_account))).all()
        out += [["unmapped_usage", a or "?", "", str(q(v or ZERO))] for a, v in unmapped]
        unbilled = (await session.execute(
            select(CanonicalCostRecord.customer_id, CanonicalCostRecord.billing_period_start,
                   func.sum(CanonicalCostRecord.provider_billed))
            .where(*cw, CanonicalCostRecord.customer_id.isnot(None),
                   CanonicalCostRecord.line_item_type == "usage")
            .group_by(CanonicalCostRecord.customer_id,
                      CanonicalCostRecord.billing_period_start))).all()
        for cust_id, pstart, amt in unbilled:
            issued = int((await session.execute(
                select(func.count(Invoice.id)).where(
                    Invoice.customer_id == cust_id, Invoice.deleted_at.is_(None),
                    Invoice.period_start == pstart,
                    Invoice.status.in_(("issued", "exported", "paid_or_settled", "corrected")))
            )).scalar_one())
            if not issued:
                cust = await session.get(Customer, cust_id)
                out.append(["unbilled_usage", cust.display_name if cust else str(cust_id),
                            pstart.isoformat()[:10], str(q(amt or ZERO))])
        return (["signal", "subject", "period", "amount"], out)

    if report == "contract_expiration":
        from datetime import timedelta

        from app.models.contracts import Contract, ContractVersion

        horizon = datetime.now(UTC) + timedelta(days=60)
        rows = (await session.execute(
            select(ContractVersion, Contract.code, Customer.display_name)
            .join(Contract, Contract.id == ContractVersion.contract_id)
            .join(Customer, Customer.id == Contract.customer_id)
            .where(ContractVersion.org_path.like(root + "%"),
                   ContractVersion.status == "active",
                   ContractVersion.effective_end.isnot(None),
                   ContractVersion.effective_end <= horizon)
            .order_by(ContractVersion.effective_end))).all()
        return (["contract", "customer", "version", "ends"],
                [[c, cust, f"v{v.version_number}", v.effective_end.isoformat()[:10]]
                 for v, c, cust in rows])

    if report == "tag_compliance":
        total = int((await session.execute(
            select(func.count(CanonicalCostRecord.id)).where(*cw,
                CanonicalCostRecord.line_item_type == "usage"))).scalar_one())
        missing = int((await session.execute(
            select(func.count(CanonicalCostRecord.id)).where(*cw,
                CanonicalCostRecord.line_item_type == "usage")
            .where(CanonicalCostRecord.application.is_(None)
                   | CanonicalCostRecord.owner.is_(None)
                   | CanonicalCostRecord.cost_center.is_(None)))).scalar_one())
        amount_missing = Decimal(str((await session.execute(
            select(func.coalesce(func.sum(CanonicalCostRecord.provider_billed), 0)).where(*cw,
                CanonicalCostRecord.line_item_type == "usage")
            .where(CanonicalCostRecord.application.is_(None)
                   | CanonicalCostRecord.owner.is_(None)
                   | CanonicalCostRecord.cost_center.is_(None)))).scalar_one() or 0))
        return (["metric", "value"],
                [["total_usage_rows", str(total)],
                 ["rows_missing_tags", str(missing)],
                 ["amount_missing_tags", str(q(amount_missing))],
                 ["coverage_pct", str(q((Decimal(total - missing) / Decimal(total) * 100)
                                        if total else ZERO))]])

    raise HTTPException(404, detail={"code": "unknown_report", "available": sorted(REPORTS)})


@router.get("/reports/catalog")
async def report_catalog(principal: Principal):
    if not principal.can("report.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    return [{"key": k, "description": v} for k, v in REPORTS.items()]


@router.post("/reports/{report_key}/run")
async def run_report(report_key: str, body: ReportRequest, session: SessionDep,
                     principal: Principal, fmt: str = Query("csv", pattern="^(csv|json)$")):
    """On-demand report: returns the file inline and records an audited
    ExportJob (data-export audit requirement)."""
    if not principal.can("report.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    if report_key not in REPORTS:
        raise HTTPException(404, detail={"code": "unknown_report", "available": sorted(REPORTS)})
    root = principal.scope_prefixes[0]
    await set_org_scope(session, root)
    header, rows = await _rows(report_key, session, principal, root, body)

    job = ExportJob(kind=f"report_{report_key}",
                    parameters={"period_start": body.period_start.isoformat() if body.period_start else None,
                                "period_end": body.period_end.isoformat() if body.period_end else None,
                                "rows": len(rows)},
                    status="completed", requested_by=principal.user_id,
                    completed_at=datetime.now(UTC),
                    org_path=root, org_id=principal.org_id)
    session.add(job)
    await record_audit(session, principal, action="export.data", org_path=root,
                       summary=f"Report '{report_key}' exported ({len(rows)} rows)",
                       entity_type="export_job", entity_id=job.id)
    await session.commit()

    if fmt == "json":
        return {"report": report_key, "header": header, "rows": rows,
                "generated_at": datetime.now(UTC).isoformat()}
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    from fastapi import Response
    return Response(content=buf.getvalue().encode("utf-8"), media_type="text/csv",
                    headers={"content-disposition":
                             f'attachment; filename="{report_key}-{datetime.now(UTC):%Y%m%d}.csv"'})

# ------------------------------------------------------- scheduled reports

class ScheduleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    report_key: str
    cadence: str = Field(default="monthly", pattern="^(monthly|weekly)$")
    day_of_month: int = Field(default=1, ge=1, le=28)
    day_of_week: int = Field(default=1, ge=0, le=6)
    hour_utc: int = Field(default=6, ge=0, le=23)
    recipients: list[str] = Field(default_factory=list, max_length=20)
    period_offset_months: int = Field(default=1, ge=1, le=12)


@router.get("/reports/schedules")
async def list_schedules(session: SessionDep, principal: Principal):
    if not principal.can("report.read"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from app.models.invoices import ReportSchedule

    root = principal.scope_prefixes[0]
    rows = (await session.execute(
        select(ReportSchedule).where(ReportSchedule.org_path.like(root + "%"),
                                     ReportSchedule.deleted_at.is_(None))
        .order_by(ReportSchedule.next_run_at))).scalars()
    return [{
        "id": str(x.id), "name": x.name, "report_key": x.report_key,
        "cadence": x.cadence, "day_of_month": x.day_of_month, "day_of_week": x.day_of_week,
        "hour_utc": x.hour_utc, "recipients": x.recipients,
        "period_offset_months": x.period_offset_months, "enabled": x.enabled,
        "last_run_at": x.last_run_at.isoformat() if x.last_run_at else None,
        "next_run_at": x.next_run_at.isoformat() if x.next_run_at else None,
    } for x in rows]


@router.post("/reports/schedules", status_code=201, dependencies=[CSRF])
async def create_schedule(body: ScheduleCreate, session: SessionDep, principal: Principal):
    if not principal.can("report.schedule"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from datetime import datetime

    from app.models.invoices import ReportSchedule
    from app.services.schedules import compute_next_run, validate_schedule_fields

    if body.report_key not in REPORTS:
        raise HTTPException(400, detail={"code": "unknown_report", "available": sorted(REPORTS)})
    err = validate_schedule_fields(body.cadence, body.day_of_month, body.day_of_week, body.hour_utc)
    if err:
        raise HTTPException(400, detail={"code": "bad_schedule", "message": err})
    root = principal.scope_prefixes[0]
    await set_org_scope(session, root)
    now = datetime.now(UTC)
    x = ReportSchedule(
        name=body.name, report_key=body.report_key, cadence=body.cadence,
        day_of_month=body.day_of_month, day_of_week=body.day_of_week,
        hour_utc=body.hour_utc, recipients=[r.strip().lower() for r in body.recipients if r.strip()],
        period_offset_months=body.period_offset_months,
        enabled=True, created_by=principal.user_id,
        next_run_at=compute_next_run(
            _DraftSchedule(body.cadence, body.day_of_month, body.day_of_week, body.hour_utc), now),
        org_path=root, org_id=principal.org_id,
    )
    session.add(x)
    await record_audit(session, principal, action="report.schedule_created", org_path=root,
                       summary=f"Scheduled report '{body.name}' ({body.report_key}, {body.cadence})",
                       entity_type="report_schedule", entity_id=x.id)
    await session.commit()
    return {"id": str(x.id), "next_run_at": x.next_run_at.isoformat()}


class _DraftSchedule:
    def __init__(self, cadence: str, dom: int, dow: int, hour: int) -> None:
        self.cadence, self.day_of_month, self.day_of_week, self.hour_utc = cadence, dom, dow, hour


@router.post("/reports/schedules/{schedule_id}/toggle", dependencies=[CSRF])
async def toggle_schedule(schedule_id: uuid.UUID, session: SessionDep, principal: Principal):
    if not principal.can("report.schedule"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "forbidden"})
    from app.models.invoices import ReportSchedule

    x = await session.get(ReportSchedule, schedule_id)
    if x is None:
        raise HTTPException(404, detail={"code": "not_found"})
    root = principal.scope_prefixes[0]
    if not principal.is_platform_admin and not x.org_path.startswith(root):
        raise HTTPException(404, detail={"code": "not_found"})
    await set_org_scope(session, x.org_path)
    x.enabled = not x.enabled
    await record_audit(session, principal, action="report.schedule_toggled", org_path=x.org_path,
                       summary=f"Scheduled report '{x.name}' {'enabled' if x.enabled else 'disabled'}",
                       entity_type="report_schedule", entity_id=x.id)
    await session.commit()
    return {"ok": True, "enabled": x.enabled}
