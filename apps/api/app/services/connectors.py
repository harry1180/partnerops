"""Connector scheduling service (Phase 3): multi-cloud ingestion plumbing.

Real connectors (AWS CUR on S3, Azure Cost Management exports) are optional,
credential-gated integrations. In local demo mode a connector runs in
`synthetic` mode: "run now" imports the deterministic fixture file for the
billing period and records the ingestion against the connector — every field
in the status feed (last_ingest_at, last file, next_due_at) is evidence-based.

Live fetch is not implemented and no code path pretends otherwise: connectors
expose fetch_available=False (ADR-0016).

Cadence math reuses schedules.compute_next_run (monthly day-of-month or
weekly day-of-week at hour_utc).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_bypass_scope, set_org_scope
from app.models.connectors import ProviderConnector
from app.services.audit_service import record_audit
from app.services.authz import RequestPrincipal
from app.services.schedules import ScheduleLike, compute_next_run


def _aware(dt: datetime | None) -> datetime | None:
    """sqlite has no tz-aware storage: timestamps come back naive-UTC.
    Normalize so service code and tests can compare against aware datetimes
    on every dialect (the same normalization the scheduled-report tests do)."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@dataclass
class _ConnectorSchedule(ScheduleLike):
    """Adapter view of a connector row for the shared cadence math."""

    cadence: str
    day_of_month: int
    day_of_week: int
    hour_utc: int


def connector_schedule_row(c: ProviderConnector) -> ScheduleLike:
    return _ConnectorSchedule(
        cadence=c.cadence if c.cadence in ("monthly", "weekly") else "monthly",
        day_of_month=c.day_of_month, day_of_week=3, hour_utc=c.hour_utc,
    )


@dataclass
class ConnectorRunResult:
    connector_id: uuid.UUID
    file_id: uuid.UUID | None
    status: str  # ingested | no_fixture | error
    canonical: int = 0
    detail: dict[str, Any] | None = None


async def list_connectors(session: AsyncSession, org_path_prefix: str) -> list[dict]:
    rows = (
        await session.execute(
            select(ProviderConnector).where(
                ProviderConnector.org_path.like(org_path_prefix + "%"),
                ProviderConnector.deleted_at.is_(None),
            ).order_by(ProviderConnector.provider_code, ProviderConnector.name)
        )
    ).scalars().all()
    now = datetime.now(UTC)
    out = []
    for c in rows:
        due = _aware(c.next_due_at)
        out.append({
            "id": str(c.id), "name": c.name, "provider": c.provider_code,
            "kind": c.connector_kind, "mode": c.mode,
            "billing_account_ref": c.billing_account_ref,
            "cadence": c.cadence, "enabled": c.enabled,
            "day_of_month": c.day_of_month, "hour_utc": c.hour_utc,
            "fetch_available": False,  # live connectors are a later phase
            "last_ingest_at": c.last_ingest_at.isoformat() if c.last_ingest_at else None,
            "last_file_id": str(c.last_file_id) if c.last_file_id else None,
            "next_due_at": due.isoformat() if due else None,
            "due_now": bool(c.enabled and due and due <= now),
            "config": c.config,
        })
    return out


async def create_connector(
    session: AsyncSession, principal: RequestPrincipal, *, name: str,
    provider_code: str, connector_kind: str, billing_account_ref: str,
    cadence: str, day_of_month: int, hour_utc: int, config: dict,
) -> ProviderConnector:
    org_path = principal.org_path
    await set_org_scope(session, org_path)
    dup = (
        await session.execute(
            select(ProviderConnector).where(
                ProviderConnector.org_path == org_path,
                ProviderConnector.provider_code == provider_code,
                ProviderConnector.billing_account_ref == billing_account_ref,
                ProviderConnector.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise ValueError(f"connector already configured for {provider_code}/{billing_account_ref}")
    c = ProviderConnector(
        org_id=principal.org_id, org_path=org_path, name=name,
        provider_code=provider_code, connector_kind=connector_kind,
        mode="synthetic", billing_account_ref=billing_account_ref,
        cadence=cadence, day_of_month=day_of_month, hour_utc=hour_utc,
        config=config, created_by=principal.user_id,
    )
    c.next_due_at = compute_next_run(connector_schedule_row(c),
                                     after=datetime.now(UTC) - timedelta(minutes=1))
    session.add(c)
    await session.flush()
    await record_audit(
        session, principal, action="integration.connector_created", org_path=org_path,
        summary=f"Connector {name} ({provider_code}/{billing_account_ref}) configured",
        entity_type="provider_connector", entity_id=c.id,
        detail={"mode": "synthetic", "cadence": cadence},
    )
    await session.commit()
    return c


async def toggle_connector(session: AsyncSession, principal: RequestPrincipal,
                           cid: uuid.UUID, enabled: bool) -> ProviderConnector:
    await set_org_scope(session, principal.org_path)
    c = await session.get(ProviderConnector, cid)
    if c is None or c.deleted_at is not None or not c.org_path.startswith(principal.org_path):
        raise LookupError("connector not found")
    c.enabled = enabled
    if enabled and c.next_due_at is None:
        c.next_due_at = compute_next_run(connector_schedule_row(c), after=datetime.now(UTC))
    await record_audit(
        session, principal, action="integration.connector_updated", org_path=c.org_path,
        summary=f"Connector {c.name} {'enabled' if enabled else 'disabled'}",
        entity_type="provider_connector", entity_id=c.id,
    )
    await session.commit()
    return c


def _fixture_book() -> dict[str, dict[str, dict[str, str]]]:
    """provider → billing account ref → period label → csv text."""
    from app.ingestion import synthetic_aws, synthetic_azure

    return {
        "aws": synthetic_aws.build_all(),
        "azure": synthetic_azure.build_all(),
    }


async def run_connector_now(
    session: AsyncSession, principal: RequestPrincipal, cid: uuid.UUID,
    period_label: str, correlation_id: str | None = None,
) -> ConnectorRunResult:
    """Synthetic-mode connector run: ingest this period's fixture file and
    record evidence on the connector row. Idempotent via the file sha256."""
    from app.services import ingest_service

    await set_org_scope(session, principal.org_path)
    c = await session.get(ProviderConnector, cid)
    if c is None or c.deleted_at is not None or not c.org_path.startswith(principal.org_path):
        raise LookupError("connector not found")

    text_body = _fixture_book().get(c.provider_code, {}).get(c.billing_account_ref, {}).get(period_label)
    if text_body is None:
        known = sorted(_fixture_book().get(c.provider_code, {}).get(c.billing_account_ref, {}))
        return ConnectorRunResult(connector_id=c.id, file_id=None, status="no_fixture",
                                  detail={"known_periods": known})
    summary = await ingest_service.ingest_csv(
        session, org_id=c.org_id, org_path=c.org_path, provider_code=c.provider_code,
        parser_version=1, filename=f"{period_label}.csv",
        body=text_body.encode(),
        object_key=f"fixtures/{c.provider_code}/{c.billing_account_ref}/{period_label}.csv",
        source="synthetic", correlation_id=correlation_id, actor_user_id=principal.user_id,
    )
    now = datetime.now(UTC)
    c.last_ingest_at = now
    c.last_file_id = summary.file_id
    c.next_due_at = compute_next_run(connector_schedule_row(c), after=now)
    await record_audit(
        session, principal, action="integration.connector_ran", org_path=c.org_path,
        summary=(f"Connector {c.name} ingested {period_label}: {summary.canonical} canonical rows"
                 + (" (duplicate file skipped)" if summary.skipped_duplicate_file else "")),
        entity_type="provider_connector", entity_id=c.id,
        detail={"file_id": str(summary.file_id), "provider": c.provider_code},
        correlation_id=correlation_id,
    )
    await session.commit()
    return ConnectorRunResult(connector_id=c.id, file_id=summary.file_id, status="ingested",
                              canonical=summary.canonical,
                              detail={"skipped_duplicate_file": summary.skipped_duplicate_file,
                                      "duplicates": summary.duplicates,
                                      "quarantined": summary.quarantined})


async def run_due_connectors(session: AsyncSession,
                             now: datetime | None = None) -> list[ConnectorRunResult]:
    """Beat entry (every 15 min): for each due connector, ingest the fixture
    for the previous full calendar month (the period billing teams close on).
    Cross-tenant discovery uses the documented bypass scope; each connector
    then runs under its own org scope — same pattern as run_due_schedules."""
    from app.services import ingest_service
    from app.services.schedules import _period_for

    now = now or datetime.now(UTC)
    await set_bypass_scope(session)
    due = list((
        await session.execute(
            select(ProviderConnector).where(
                ProviderConnector.enabled.is_(True),
                ProviderConnector.deleted_at.is_(None),
                ProviderConnector.next_due_at.isnot(None),
                ProviderConnector.next_due_at <= now,
            ).order_by(ProviderConnector.next_due_at)
        )
    ).scalars().all())
    await session.commit()  # end the root-scope transaction

    results: list[ConnectorRunResult] = []
    if not due:
        return results
    fixtures = _fixture_book()
    period_start, _ = _period_for(now, 1)
    label = f"{period_start:%Y-%m}"
    for c in due:
        await set_org_scope(session, c.org_path)
        text_body = fixtures.get(c.provider_code, {}).get(c.billing_account_ref, {}).get(label)
        if text_body is None:
            c.next_due_at = compute_next_run(connector_schedule_row(c), after=now)
            results.append(ConnectorRunResult(connector_id=c.id, file_id=None, status="no_fixture"))
            await session.commit()
            continue
        summary = await ingest_service.ingest_csv(
            session, org_id=c.org_id, org_path=c.org_path, provider_code=c.provider_code,
            parser_version=1, filename=f"{label}.csv", body=text_body.encode(),
            object_key=f"fixtures/{c.provider_code}/{c.billing_account_ref}/{label}.csv",
            source="synthetic", correlation_id=None, actor_user_id=None,
        )
        c.last_ingest_at = now
        c.last_file_id = summary.file_id
        c.next_due_at = compute_next_run(connector_schedule_row(c), after=now)
        await record_audit(
            session, None, action="integration.connector_ran", org_path=c.org_path,
            actor_kind="system",
            summary=f"Scheduled connector {c.name} ingested {label}: {summary.canonical} rows",
            entity_type="provider_connector", entity_id=c.id,
            detail={"file_id": str(summary.file_id), "scheduled": True},
        )
        results.append(ConnectorRunResult(connector_id=c.id, file_id=summary.file_id,
                                          status="ingested", canonical=summary.canonical))
        await session.commit()  # per-connector transaction: one failure ≠ all
    return results


async def connector_status_counts(session: AsyncSession, org_path_prefix: str) -> dict[str, int]:
    rows = (
        await session.execute(
            select(ProviderConnector.provider_code, func.count(ProviderConnector.id))
            .where(ProviderConnector.org_path.like(org_path_prefix + "%"),
                   ProviderConnector.deleted_at.is_(None))
            .group_by(ProviderConnector.provider_code)
        )
    ).all()
    return {p: int(n) for p, n in rows}
