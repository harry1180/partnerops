"""Scheduled FinOps/governance passes (Phase 4, worker-side).

Discovers org paths that own canonical cost rows (bypass scope for the
cross-tenant pass — same documented pattern as connectors/schedules),
collapsing to the minimum covering set of subtrees, then runs
anomaly/recommendation/governance per subtree under its own scope. Each
subtree commits separately so one failure can't poison the rest; failures
are logged, never swallowed silently.
"""

from __future__ import annotations

from sqlalchemy import distinct, select

from app.core.logging import get_logger
from app.db.rls import set_bypass_scope, set_org_scope
from app.models.cost import CanonicalCostRecord
from app.services import alerts, anomaly, governance
from app.services import recommendations as rsvc
from app.services.audit_service import record_audit

log = get_logger(__name__)


async def covering_org_paths(session) -> list[str]:
    """Distinct org paths owning canonical usage, collapsed so a parent path
    absorbs its children (each pass should run once per maximal subtree)."""
    await set_bypass_scope(session)
    result = await session.execute(
        select(distinct(CanonicalCostRecord.org_path))
        .where(CanonicalCostRecord.line_item_type.in_(("usage", "support")))
    )
    paths = list(result.scalars().all())
    topmost: list[str] = []
    for p in sorted(set(paths)):
        if not any(t != p and p.startswith(t) for t in topmost):
            topmost.append(p)
    return topmost


async def run_all_org_passes(session) -> int:
    orgs = await covering_org_paths(session)
    await session.commit()  # end bypass-scope transaction
    done = 0
    for org_path in orgs:
        try:
            await set_org_scope(session, org_path)
            a_res = await anomaly.run_anomaly_pass(session, org_path)
            r_res = await rsvc.run_recommendation_pass(session, org_path)
            realized = await rsvc.realize_savings(session, org_path)
            g_res = await governance.evaluate_policies(session, org_path)
            al_res = await alerts.evaluate_budget_alerts(session, org_path)
            await record_audit(
                session, None, action="finops.passes_ran", org_path=org_path,
                actor_kind="system",
                summary=(f"Nightly FinOps pass: {a_res.created} anomalies, "
                         f"{r_res.created} recommendations ({realized} realized), "
                         f"{g_res.findings_open} open findings, "
                         f"{al_res.opened} budget alerts opened / {al_res.closed} recovered"),
                entity_type="governance_policy", entity_id=None,
                detail={"anomalies_created": a_res.created,
                        "recs_created": r_res.created, "realized": realized,
                        "findings_new": g_res.findings_new,
                        "findings_open": g_res.findings_open,
                        "alerts_opened": al_res.opened,
                        "alerts_closed": al_res.closed},
            )
            await session.commit()
            done += 1
        except Exception as exc:  # one bad tenant ≠ the whole run; log loudly
            await session.rollback()
            log.error("finops_pass_failed", org_path=org_path, error=str(exc)[:400])
    return done
