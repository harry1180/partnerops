"""Spend alerts (Phase 7): budget threshold breaches become real notifications.

Every active budget is evaluated against actual spend (Phase 4 burn math).
When projected/actual spend crosses alert_threshold_pct we open an *alert
episode*: queue the `budget.over_threshold` webhook event and email the
users in scope who hold the right role (partner alert roles for partner
budgets; the customer's own admins for customer-scoped budgets). One
episode per breach: re-evaluating while still breached sends nothing; when
spend falls back below the threshold the episode closes and the next
breach alerts again (re-arm). State lives in budgets.alert_state — a JSON
column, so no new tables and no new RLS surface.

Numbers in every notification come from budget_status() — the same
computation the UI shows; nothing is invented, and closed episodes record
when they recovered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.rls import set_org_scope
from app.models.approvals import NotificationOutbox
from app.models.auth import Role, User, UserRoleAssignment
from app.models.billing_core import Customer
from app.models.finops import Budget
from app.models.org import Organization
from app.services import budgets as bsvc
from app.services.webhooks import queue_event

log = get_logger(__name__)

ALERT_ROLE_KEYS = ("msp_admin", "distributor_admin", "finops_analyst")
CUSTOMER_ALERT_ROLE_KEYS = ("customer_admin",)


@dataclass
class AlertPassResult:
    evaluated: int = 0
    opened: int = 0      # new episodes (webhook + emails queued)
    closed: int = 0      # recovered episodes
    still_open: int = 0  # re-evaluated while breached — no duplicate alert

    def as_dict(self) -> dict:
        return {"evaluated": self.evaluated, "opened": self.opened,
                "closed": self.closed, "still_open": self.still_open}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _recipients(session: AsyncSession, budget: Budget,
                      org_path: str) -> list[str]:
    """Email targets for one episode. Partner alert roles in the evaluated
    subtree always get their budget alerts; customer-scoped budgets ALSO
    notify that customer's own admins (their spend, their alert) when such
    users exist. Emails are role-routed, never broadcast."""
    orgs = {str(o.id): o.path for o in
            (await session.execute(select(Organization))).scalars()}
    out: list[str] = []

    partner_rows = (await session.execute(
        select(User.email, User.home_org_id)
        .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
        .join(Role, Role.id == UserRoleAssignment.role_id)
        .where(User.deleted_at.is_(None), User.status == "active",
               Role.key.in_(ALERT_ROLE_KEYS))
    )).all()
    for email, hid in partner_rows:
        if email not in out and orgs.get(str(hid), "").startswith(org_path):
            out.append(email)

    if budget.scope_kind == "customer" and budget.customer_id is not None:
        cust = await session.get(Customer, budget.customer_id)
        if cust is not None:
            cust_org = (await session.execute(
                select(Organization).where(Organization.path == cust.org_path)
            )).scalars().first()
            if cust_org is not None:
                rows = (await session.execute(
                    select(User.email)
                    .join(UserRoleAssignment, UserRoleAssignment.user_id == User.id)
                    .join(Role, Role.id == UserRoleAssignment.role_id)
                    .where(User.deleted_at.is_(None), User.status == "active",
                           Role.key.in_(CUSTOMER_ALERT_ROLE_KEYS),
                           User.home_org_id == cust_org.id)
                )).all()
                for (email,) in rows:
                    if email not in out:
                        out.append(email)
    return sorted(out)


async def evaluate_budget_alerts(session: AsyncSession, org_path: str,
                                 now: datetime | None = None) -> AlertPassResult:
    """One alert pass over an org subtree. Caller owns the transaction;
    opened episodes queue webhook deliveries + outbox emails (the
    integrations sweep transports both)."""
    await set_org_scope(session, org_path)
    now = _aware(now) if now else datetime.now(UTC)
    budgets = list((await session.execute(
        select(Budget).where(Budget.org_path.like(org_path + "%"),
                             Budget.deleted_at.is_(None),
                             Budget.period_end >= now - timedelta(days=45))
        .order_by(Budget.period_start)
    )).scalars())
    result = AlertPassResult()
    for b in budgets:
        result.evaluated += 1
        status = await bsvc.budget_status(session, org_path, b, now)
        state = dict(b.alert_state or {})
        breached_now = status.over_threshold or status.over_budget
        was = bool(state.get("breached"))
        if breached_now and not was:
            state = {
                "breached": True,
                "opened_at": now.isoformat(),
                "last_alert_at": now.isoformat(),
                "alert_count": int(state.get("alert_count", 0)) + 1,
            }
            result.opened += 1
            cust = await session.get(Customer, b.customer_id) if b.customer_id else None
            label = cust.display_name if cust else "organization"
            payload = {
                "budget_id": str(b.id), "budget_name": b.name,
                "scope_kind": b.scope_kind,
                "customer_id": str(b.customer_id) if b.customer_id else None,
                "customer": label,
                "amount": str(status.budget.amount),
                "actual": str(status.actual),
                "projected_total": str(status.projected_total),
                "pct_of_budget": str(status.pct_of_budget),
                "projected_pct": str(status.projected_pct),
                "alert_threshold_pct": b.alert_threshold_pct,
                "period": (f"{_aware(b.period_start):%Y-%m-%d}"
                           f"→{_aware(b.period_end):%Y-%m-%d}"),
                "over_budget": status.over_budget,
                "episode": state["alert_count"],
            }
            await queue_event(session, b.org_path, "budget.over_threshold",
                              payload, scope=org_path)
            for email in await _recipients(session, b, org_path):
                session.add(NotificationOutbox(
                    channel="email", recipient=email,
                    subject=(f"[Budget alert] {b.name} ({label}) at "
                             f"{status.pct_of_budget}% of "
                             f"{status.budget.amount} {b.currency}"),
                    body=(
                        f"Budget '{b.name}' for {label} has crossed its "
                        "alert threshold.\n"
                        f"  period: {payload['period']}\n"
                        f"  actual: {status.actual}  |  projected: "
                        f"{status.projected_total}  |  budget: "
                        f"{status.budget.amount} {b.currency}\n"
                        f"  {status.pct_of_budget}% spent, projected "
                        f"{status.projected_pct}% (alert at "
                        f"{b.alert_threshold_pct}%)\n"
                        + ("  OVER BUDGET.\n" if status.over_budget else "")
                        + "\nReview under Budgets & Anomalies — this alert "
                        "fires once per episode.\n"),
                    kind="budget_alert", entity_type="budget", entity_id=b.id,
                    org_path=b.org_path, org_id=b.org_id))
        elif breached_now and was:
            result.still_open += 1
        elif not breached_now and was:
            state = {**state, "breached": False, "recovered_at": now.isoformat()}
            result.closed += 1
        # write back a NEW dict — plain attribute assignment is change-tracked
        b.alert_state = state
    await session.flush()
    if result.opened or result.closed:
        log.info("budget_alerts", org_path=org_path[:60], **result.as_dict())
    return result
