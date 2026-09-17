"""Scheduled reports service.

A schedule stores a cadence (monthly day-of-month or weekly day-of-week, at
hour_utc). The Celery beat task calls run_due_schedules(): for every enabled
schedule whose next_run_at has passed, it generates the report CSV over the
org's data, stores it in object storage, records an audited ExportJob, and
queues one email per recipient through the notification outbox. Email
TRANSPORT is an integration boundary (Phase 5) — outbox rows are real,
delivery is not faked.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_bypass_scope, set_org_scope
from app.models.approvals import NotificationOutbox
from app.models.invoices import ExportJob, ReportSchedule
from app.services.audit_service import record_audit
from app.services.authz import RequestPrincipal


def _system_principal(sched: OwnedLike) -> RequestPrincipal:
    """Synthetic principal for worker-side generation: scoped to the
    schedule's org, report.read only. Never usable by web routes."""
    return RequestPrincipal(
        user_id=sched.created_by or uuid.UUID(int=0),
        email="system@cloudpartnerops.local",
        org_id=sched.org_id, org_path=sched.org_path, org_kind="system",
        roles=frozenset({"system"}), permissions=frozenset({"report.read", "cost.read"}),
        scope_prefixes=(sched.org_path,),
    )


def _period_for(now: datetime, offset_months: int) -> tuple[datetime, datetime]:
    """The billing period covered: `period_offset_months` back from the run
    date, i.e. the previous month for the default offset of 1."""
    year, month = now.year, now.month
    for _ in range(offset_months):
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + (month == 12), (month % 12) + 1, 1, tzinfo=UTC)
    return start, end


def compute_next_run(sched: ScheduleLike, after: datetime) -> datetime:
    """Deterministic next occurrence strictly after `after` (UTC)."""
    if sched.cadence == "monthly":
        y, m = after.year, after.month
        candidate = _safe_date(y, m, sched.day_of_month, sched.hour_utc)
        if candidate <= after:
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            candidate = _safe_date(y, m, sched.day_of_month, sched.hour_utc)
        return candidate
    # weekly: day_of_week 0=Mon..6=Sun
    delta = (sched.day_of_week - after.weekday()) % 7
    candidate = (after + timedelta(days=delta)).replace(
        hour=sched.hour_utc, minute=0, second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


def _safe_date(y: int, m: int, d: int, hour: int) -> datetime:
    # day_of_month is constrained 1..28 so this never overflows
    return datetime(y, m, min(d, 28), hour, tzinfo=UTC)


class ScheduleLike(Protocol):
    """Anything with cadence fields: the ORM row or the detached _Due."""
    cadence: str
    day_of_month: int
    day_of_week: int
    hour_utc: int


class OwnedLike(ScheduleLike, Protocol):
    org_id: uuid.UUID
    org_path: str
    created_by: uuid.UUID | None


@dataclass
class ScheduleRunResult:
    schedule_id: uuid.UUID
    report_key: str
    object_key: str | None
    rows: int
    emailed: int
    error: str | None = None


@dataclass
class _Due:
    """Detached snapshot of one due schedule (cross-tenant scan, then each
    row processed inside its own org-scoped transaction)."""
    id: uuid.UUID
    name: str
    report_key: str
    cadence: str
    day_of_month: int
    day_of_week: int
    hour_utc: int
    recipients: list[str]
    period_offset_months: int
    org_id: uuid.UUID
    org_path: str
    created_by: uuid.UUID | None


async def run_due_schedules(session: AsyncSession, now: datetime | None = None) -> list[ScheduleRunResult]:
    """One pass over due schedules. Safe to call repeatedly (idempotent per
    run window via next_run_at advancement).

    The due-scan runs at ROOT scope (set_bypass_scope): this is the trusted
    worker, not a request path — a tenant-scoped GUC would (correctly) see
    zero rows under RLS. Each schedule is then processed with the scope
    re-bound to its own org, so every write stays inside that tenant.
    """
    from app.api.v1.reports import ReportRequest, _rows

    now = now or datetime.now(UTC)
    await set_bypass_scope(session)
    due = [_Due(
        id=x.id, name=x.name, report_key=x.report_key, cadence=x.cadence,
        day_of_month=x.day_of_month, day_of_week=x.day_of_week, hour_utc=x.hour_utc,
        recipients=list(x.recipients or []), period_offset_months=x.period_offset_months,
        org_id=x.org_id, org_path=x.org_path, created_by=x.created_by,
    ) for x in (await session.execute(
        select(ReportSchedule).where(
            ReportSchedule.enabled.is_(True),
            ReportSchedule.deleted_at.is_(None),
            ReportSchedule.next_run_at.isnot(None),
            ReportSchedule.next_run_at <= now,
        ).order_by(ReportSchedule.next_run_at)
    )).scalars()]
    await session.commit()  # end the root-scope transaction

    results: list[ScheduleRunResult] = []
    for sched in due:
        await set_org_scope(session, sched.org_path)
        principal = _system_principal(sched)
        period_start, period_end = _period_for(now, sched.period_offset_months)
        try:
            header, rows = await _rows(
                sched.report_key, session, principal, sched.org_path,
                ReportRequest(period_start=period_start, period_end=period_end))
        except Exception as exc:  # report unknown or data error
            await session.execute(
                update(ReportSchedule).where(ReportSchedule.id == sched.id)
                .values(next_run_at=compute_next_run(sched, now)))
            session.add(ExportJob(
                kind=f"report_{sched.report_key}",
                parameters={"schedule_id": str(sched.id), "error": str(exc)[:400]},
                status="failed", requested_by=sched.created_by,
                completed_at=now, org_path=sched.org_path, org_id=sched.org_id))
            results.append(ScheduleRunResult(sched.id, sched.report_key, None, 0, 0,
                                             error=str(exc)[:200]))
            await session.commit()
            continue

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        body = buf.getvalue().encode("utf-8")
        object_key = f"reports/{sched.org_id}/{sched.report_key}-{period_start:%Y%m}.csv"
        sha = None
        storage_error: str | None = None
        try:
            from app.services.storage import get_storage, sha256_bytes
            get_storage().put_bytes(object_key, body, "text/csv")
            sha = sha256_bytes(body)
            object_key_out: str | None = object_key
        except Exception as exc:
            # storage unavailable: job still audited, marked failed with cause
            object_key_out = None
            storage_error = str(exc)[:300]

        job = ExportJob(
            kind=f"report_{sched.report_key}",
            parameters={"schedule_id": str(sched.id), "period": f"{period_start:%Y-%m}",
                        "rows": len(rows), "scheduled": True,
                        **({"error": storage_error} if storage_error else {})},
            status="completed" if object_key_out else "failed",
            requested_by=sched.created_by, object_key=object_key_out, sha256=sha,
            completed_at=now, org_path=sched.org_path, org_id=sched.org_id)
        session.add(job)

        emailed = 0
        for rcpt in sched.recipients[:20]:
            session.add(NotificationOutbox(
                channel="email", recipient=rcpt,
                subject=f"[{sched.name}] {sched.report_key} report — {period_start:%B %Y}",
                body=(f"Your scheduled '{sched.report_key}' report for {period_start:%Y-%m} is ready.\n"
                      f"Rows: {len(rows)}\nStored at: {object_key_out or '(object storage unavailable)'}\n"
                      f"— {sched.name} via Cloud PartnerOps"),
                kind="report", entity_type="export_job", entity_id=job.id,
                org_path=sched.org_path, org_id=sched.org_id))
            emailed += 1

        await record_audit(session, None, action="report.schedule_run", org_path=sched.org_path,
                           summary=f"Scheduled report '{sched.report_key}' for {sched.org_path[:40]}"
                                   f" — {len(rows)} rows, {emailed} notifications queued",
                           entity_type="report_schedule", entity_id=sched.id,
                           detail={"export_job_id": str(job.id),
                                   "object_key": object_key_out},
                           correlation_id=None, actor_kind="system")
        await session.execute(
            update(ReportSchedule).where(ReportSchedule.id == sched.id)
            .values(last_run_at=now, next_run_at=compute_next_run(sched, now)))
        results.append(ScheduleRunResult(sched.id, sched.report_key, object_key_out,
                                         len(rows), emailed))
        await session.commit()  # per-schedule transaction: one failure ≠ all
    return results


def validate_schedule_fields(cadence: str, day_of_month: int, day_of_week: int,
                             hour_utc: int) -> str | None:
    """Returns an error string or None. Kept separate so API + tests share it."""
    if cadence not in ("monthly", "weekly"):
        return "cadence must be monthly or weekly"
    if not 1 <= day_of_month <= 28:
        return "day_of_month must be 1..28"
    if not 0 <= day_of_week <= 6:
        return "day_of_week must be 0..6 (Mon..Sun)"
    if not 0 <= hour_utc <= 23:
        return "hour_utc must be 0..23"
    return None
