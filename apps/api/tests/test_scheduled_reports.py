"""Scheduled reports tests: cadence math (pure) + a full due-run (service)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.db.rls import set_org_scope
from app.models.invoices import ExportJob, ReportSchedule
from app.models.org import Organization
from app.services import schedules


class _S:
    def __init__(self, cadence="monthly", dom=1, dow=1, hour=6):
        self.cadence, self.day_of_month, self.day_of_week, self.hour_utc = cadence, dom, dow, hour


def test_next_run_monthly_rolls_to_next_month():
    s = _S(cadence="monthly", dom=5, hour=6)
    nxt = schedules.compute_next_run(s, datetime(2026, 9, 10, 12, tzinfo=UTC))
    assert nxt == datetime(2026, 10, 5, 6, tzinfo=UTC)


def test_next_run_monthly_same_day_later_hour():
    s = _S(cadence="monthly", dom=5, hour=18)
    nxt = schedules.compute_next_run(s, datetime(2026, 9, 5, 12, tzinfo=UTC))
    assert nxt == datetime(2026, 9, 5, 18, tzinfo=UTC)


def test_next_run_monthly_rolls_year():
    s = _S(cadence="monthly", dom=1, hour=0)
    nxt = schedules.compute_next_run(s, datetime(2026, 12, 2, tzinfo=UTC))
    assert nxt == datetime(2027, 1, 1, 0, tzinfo=UTC)


def test_next_run_weekly():
    # Python weekday(): Mon=0..Sun=6. 2026-09-16 is a Wednesday (2);
    # day_of_week=1 -> Tuesday, i.e. 2026-09-22 06:00
    s = _S(cadence="weekly", dow=1, hour=6)
    nxt = schedules.compute_next_run(s, datetime(2026, 9, 16, 12, tzinfo=UTC))
    assert nxt == datetime(2026, 9, 22, 6, tzinfo=UTC)
    assert nxt.weekday() == 1


def test_next_run_weekly_same_day_after_hour():
    s = _S(cadence="weekly", dow=2, hour=6)  # Wed 06:00
    nxt = schedules.compute_next_run(s, datetime(2026, 9, 16, 12, tzinfo=UTC))  # Wed noon
    assert nxt == datetime(2026, 9, 23, 6, tzinfo=UTC)


def test_validate_schedule_fields():
    assert schedules.validate_schedule_fields("monthly", 15, 1, 6) is None
    assert schedules.validate_schedule_fields("daily", 1, 1, 6) is not None
    assert schedules.validate_schedule_fields("monthly", 31, 1, 6) is not None
    assert schedules.validate_schedule_fields("monthly", 1, 7, 6) is not None
    assert schedules.validate_schedule_fields("monthly", 1, 1, 24) is not None


ORG = uuid.uuid4()
ORG_PATH = f"/11111111-1111-4111-8111-111111111111/{ORG}/"


class _FakeStorage:
    """Deterministic storage double: the test suite intentionally points
    object storage at an unreachable endpoint, so inject a local one."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, body: bytes, content_type: str = "application/octet-stream") -> str:
        self.objects[key] = body
        from app.services.storage import sha256_bytes
        return sha256_bytes(body)


@pytest.mark.asyncio
async def test_run_due_schedules_generates_and_advances(client, migrated_db, monkeypatch):
    from app.services import storage as storage_mod

    fake = _FakeStorage()
    monkeypatch.setattr(storage_mod, "_storage", fake)
    async with SessionLocal() as s:
        await set_org_scope(s, "/")
        if await s.get(Organization, ORG) is None:
            s.add(Organization(id=ORG, kind="reseller", name="Sched MSP",
                              path=ORG_PATH, currency="USD"))
        await s.flush()
        now = datetime(2026, 9, 15, 7, tzinfo=UTC)
        s.add(ReportSchedule(
            name="Monthly cost statement", report_key="customer_cost_statement",
            cadence="monthly", day_of_month=15, day_of_week=1, hour_utc=6,
            recipients=["finops@example.com"], period_offset_months=1, enabled=True,
            next_run_at=now.replace(hour=6),  # due one hour ago
            org_id=ORG, org_path=ORG_PATH,
        ))
        await s.commit()

    results = await _run_with_session(now)
    assert len(results) == 1
    res = results[0]
    assert res.report_key == "customer_cost_statement"
    assert res.emailed == 1
    # August 2026 period (offset 1 from September); rows exist only if data
    # was ingested into this org — fresh org => 0 rows but still completes.
    assert res.rows == 0
    assert res.error is None
    assert res.object_key and res.object_key.endswith("customer_cost_statement-202608.csv")
    assert fake.objects, "CSV body stored"
    assert fake.objects[res.object_key].startswith(b"customer,code,period")

    async with SessionLocal() as s:
        await set_org_scope(s, ORG_PATH)
        sched = (await s.execute(select(ReportSchedule))).scalars().one()
        # advanced to October 15 06:00 (sqlite reads back naive; normalize)
        got = sched.next_run_at.replace(tzinfo=UTC) if sched.next_run_at.tzinfo is None \
            else sched.next_run_at
        assert got == datetime(2026, 10, 15, 6, tzinfo=UTC)
        assert sched.last_run_at is not None
        jobs = list((await s.execute(
            select(ExportJob).where(ExportJob.parameters[
                "scheduled"].as_boolean().is_(True)))).scalars())
        assert len(jobs) == 1, jobs
        assert jobs[0].status == "completed", jobs[0].parameters.get("error")
        assert jobs[0].parameters["scheduled"] is True
        from app.models.approvals import NotificationOutbox
        # scoped to this org's report notifications (other tests legitimately
        # queue their own notifications into the shared outbox)
        outbox = list((await s.execute(
            select(NotificationOutbox).where(
                NotificationOutbox.kind == "report",
                NotificationOutbox.org_path == ORG_PATH))).scalars())
        assert len(outbox) == 1 and "August 2026" in outbox[0].subject
        from app.models.audit import AuditEvent
        acts = list((await s.execute(select(AuditEvent.action))).scalars())
        assert "report.schedule_run" in acts

    # second pass: not due again until October
    results2 = await _run_with_session(now)
    assert results2 == []


async def _run_with_session(now: datetime):
    async with SessionLocal() as s:
        return await schedules.run_due_schedules(s, now=now)
