"""Pricing service: canonical costs × contract version × rule versions →
PricingRun with per-line lineage.

Flow (all amounts Decimal, one transaction per customer-period):
1. validate contract version covers the period; compute next run number.
2. aggregate canonical records into slices by the contract's grouping keys.
3. pin rule versions (contract rule_bindings, else all published for the
   org path) and freeze them into PricingRunRuleSnapshot rows.
4. run app.engine.rules (pure) → priced slices + invoice-level residuals.
5. write PricingRunItem rows with FULL lineage: source record ids, rule
   version ids, input, formula, output, provider cost per line.

Reprocessing writes a NEW run (run_number+1, supersedes_id) and marks the
previous completed run superseded — history is never rewritten.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.rls import set_org_scope
from app.engine.money import ZERO, q
from app.engine.rules import CostSlice, EffectiveRule, apply_rules, invoice_level_adjustments
from app.models.billing_core import Customer
from app.models.contracts import BillingRule, BillingRuleVersion, Contract, ContractVersion
from app.models.cost import CanonicalCostRecord
from app.models.pricing import PricingRun, PricingRunItem, PricingRunRuleSnapshot

log = get_logger(__name__)

MAX_LINEAGE_IDS = 500  # keep items bounded; full set always in raw_billing_records


@dataclass
class RunResult:
    run_id: uuid.UUID
    status: str
    totals: dict[str, str] = field(default_factory=dict)


def _dec(v: Decimal | None) -> Decimal:
    return v if v is not None else ZERO


def _aware(dt: datetime) -> datetime:
    """SQLite test backends hand back naive datetimes; PG is always aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def run_pricing(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    contract_version_id: uuid.UUID,
    period_start: datetime,
    period_end: datetime,
    actor_user_id: uuid.UUID | None,
    correlation_id: str | None,
) -> RunResult:
    settings = get_settings()
    cv = await session.get(ContractVersion, contract_version_id)
    if cv is None:
        raise ValueError("contract version not found")
    eff_start = _aware(cv.effective_start)
    eff_end = _aware(cv.effective_end) if cv.effective_end else None
    if eff_start > period_end or (eff_end and eff_end <= period_start):
        raise ValueError("contract version does not cover the billing period")
    customer = await session.get(Customer, customer_id)
    if customer is None:
        raise ValueError("customer not found")
    # invariant: the contract version must belong to THIS customer. Without it
    # a stale UI selection could price customer A under customer B's contract
    # and write a run row RLS would reject (500) — or worse, misbill.
    contract = await session.get(Contract, cv.contract_id)
    if contract is None or contract.customer_id != customer.id:
        raise ValueError("contract version does not belong to this customer")

    await set_org_scope(session, customer.org_path)

    max_n = (
        await session.execute(
            select(func.coalesce(func.max(PricingRun.run_number), 0)).where(
                PricingRun.org_path == customer.org_path,
                PricingRun.customer_id == customer.id,
                PricingRun.period_start == period_start,
            )
        )
    ).scalar_one()
    previous = (
        await session.execute(
            select(PricingRun)
            .where(
                PricingRun.org_path == customer.org_path,
                PricingRun.customer_id == customer.id,
                PricingRun.period_start == period_start,
                PricingRun.status == "completed",
            )
            .order_by(PricingRun.run_number.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    run = PricingRun(
        customer_id=customer.id, contract_version_id=cv.id,
        period_start=period_start, period_end=period_end,
        run_number=int(max_n) + 1, supersedes_id=previous.id if previous else None,
        status="running", engine_version=settings.calc_engine_version,
        triggered_by=actor_user_id, correlation_id=correlation_id,
        org_id=cv.org_id, org_path=cv.org_path,
    )
    session.add(run)
    await session.flush()

    try:
        totals = await _price(session, run, cv, customer, settings)
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.totals = {k: str(v) for k, v in totals.items()}
        if previous is not None:
            previous.status = "superseded"
        await session.commit()
        return RunResult(run_id=run.id, status="completed",
                         totals={k: str(v) for k, v in totals.items()})
    except Exception as exc:
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        run.completed_at = datetime.now(UTC)
        await session.commit()
        raise


async def _collect_rules(
    session: AsyncSession, cv: ContractVersion, customer: Customer
) -> list[tuple[BillingRuleVersion, BillingRule]]:
    pairs: list[tuple[BillingRuleVersion, BillingRule]] = []
    bindings = cv.rule_bindings or []
    for b in bindings:
        vid = b.get("rule_version_id") if isinstance(b, dict) else b
        if not vid:
            continue  # defensive: never bind with None version id
        try:
            vid_uuid = uuid.UUID(str(vid))
        except ValueError:
            continue  # malformed binding → skipped; contract data issues surface in sandbox
        rv = await session.get(BillingRuleVersion, vid_uuid)
        if rv is None:
            continue
        rule = await session.get(BillingRule, rv.rule_id)
        if rule is not None:
            pairs.append((rv, rule))
    if pairs:
        return pairs
    rows = await session.execute(
        select(BillingRuleVersion, BillingRule)
        .join(BillingRule, BillingRule.id == BillingRuleVersion.rule_id)
        .where(
            BillingRuleVersion.status == "published",
            BillingRuleVersion.org_path == customer.org_path,
        )
    )
    return [(rv, rule) for rv, rule in rows.all()]


def _aggregate(rows: list[CanonicalCostRecord], grouping: list[str]) -> list[CostSlice]:
    groups: dict[str, CostSlice] = {}
    order: list[str] = []
    for r in rows:
        parts = []
        for dim in grouping:
            value = "unmapped" if dim == "account" and r.cloud_account_id is None else getattr(r, dim, None)
            parts.append(str(value or "—"))
        key = "|".join(f"{dim}={p}" for dim, p in zip(grouping, parts, strict=False))
        if r.is_late_adjustment:
            key = "_prior|" + key
        group = groups.get(key)
        if group is None:
            group = CostSlice(
                key=key, label=key, currency=r.currency,
                list_cost=ZERO, ondemand_equivalent=ZERO, provider_billed=ZERO,
                amortized=ZERO, effective=ZERO, net=ZERO, credit=ZERO,
                tax=ZERO, support_fee=ZERO, marketplace_fee=ZERO,
                quantity=ZERO, rule_targets=frozenset(),
                meta={"tags": {}, "grouping": dict(zip(grouping, parts, strict=False)),
                      "prior_period": key.startswith("_prior|")},
            )
            groups[key] = group
            order.append(key)
        group.list_cost += _dec(r.list_cost)
        group.ondemand_equivalent += _dec(r.ondemand_equivalent)
        group.amortized += _dec(r.amortized)
        group.effective += _dec(r.effective)
        group.net += _dec(r.net)
        group.credit += _dec(r.credit)
        group.tax += _dec(r.tax)
        group.support_fee += _dec(r.support_fee)
        group.marketplace_fee += _dec(r.marketplace_fee)
        if r.line_item_type == "usage":
            group.provider_billed += _dec(r.provider_billed)
            group.quantity += _dec(r.quantity)
            if len(group.source_record_ids) < MAX_LINEAGE_IDS:
                group.source_record_ids = group.source_record_ids + (r.source_record_id,)
        targets = set(group.rule_targets)
        for v in (r.service, r.sku, r.cost_category, r.region, r.usage_type):
            if v:
                targets.add(str(v))
        group.rule_targets = frozenset(targets)
        group.meta["tags"].update(r.tags or {})
    return [groups[k] for k in order]


async def _price(
    session: AsyncSession, run: PricingRun, cv: ContractVersion,
    customer: Customer, settings: Settings,
) -> dict[str, Decimal]:
    rows = list((
        await session.execute(
            select(CanonicalCostRecord)
            .where(
                CanonicalCostRecord.customer_id == customer.id,
                CanonicalCostRecord.billing_period_start == run.period_start,
                CanonicalCostRecord.billing_period_end == run.period_end,
            )
            .order_by(CanonicalCostRecord.id)
        )
    ).scalars())
    grouping = cv.invoice_grouping or ["service"]
    slices = _aggregate(rows, grouping)

    pairs = await _collect_rules(session, cv, customer)
    engine_rules = [
        EffectiveRule(
            rule_id=str(rule.id), version_id=str(rv.id), code=rule.code,
            name=rule.name, rule_type=rule.rule_type, priority=rv.priority,
            calc_order=rv.calc_order, applied_basis=rv.applied_basis,
            filters=rv.filters or {}, parameters=rv.parameters or {},
            version_number=rv.version_number,
        )
        for rv, rule in pairs
    ]
    for rv, rule in pairs:
        session.add(PricingRunRuleSnapshot(
            run_id=run.id, org_path=run.org_path, rule_id=rule.id,
            rule_version_id=rv.id, rule_code=rule.code, rule_type=rule.rule_type,
            version_number=rv.version_number, parameters=rv.parameters or {},
            filters=rv.filters or {}, priority=rv.priority, calc_order=rv.calc_order,
        ))

    rounding_rule = cv.rounding_rule or {}
    rounding = str(rounding_rule.get("mode", "half_up"))
    priced = apply_rules(slices, engine_rules, cv.pricing_basis)
    residual_trace = invoice_level_adjustments(priced, engine_rules, rounding)

    provider_usage = sum((p.slice.provider_billed for p in priced), ZERO)
    support_cost = sum((p.slice.support_fee for p in priced), ZERO)
    tax_cost = sum((p.slice.tax for p in priced), ZERO)
    customer_subtotal = sum((p.customer_amount for p in priced), ZERO)
    for step in residual_trace:
        customer_subtotal += step.delta
    credit_net = sum((p.slice.credit for p in priced), ZERO)
    margin = customer_subtotal - (provider_usage + support_cost + tax_cost)

    totals = {
        "provider_cost": provider_usage,
        "support_fee": support_cost,
        "tax": tax_cost,
        "customer_subtotal": customer_subtotal,
        "credit_amount": credit_net,
        "margin": margin,
    }

    item_no = 0
    for ps in priced:
        item_no += 1
        session.add(PricingRunItem(
            run_id=run.id, org_path=run.org_path, item_number=item_no,
            group_key=ps.slice.key, group_label=ps.slice.label,
            line_kind="prior_period" if ps.slice.meta.get("prior_period") else "usage",
            source_record_ids=list(ps.slice.source_record_ids),
            rule_version_ids=[t.rule_version_id for t in ps.trace],
            input_amount=q(ps.basis_amount, mode=rounding),
            output_amount=q(ps.customer_amount, mode=rounding),
            provider_cost_amount=q(ps.slice.provider_billed, mode=rounding),
            formula=" ; ".join(t.formula for t in ps.trace) or "contract basis (no rules matched)",
            calculation_trace=[
                {"rule": t.rule_code, "v": t.version_number, "action": t.action,
                 "input": str(t.input_amount), "output": str(t.output_amount),
                 "delta": str(t.delta), "formula": t.formula}
                for t in ps.trace
            ],
            currency=ps.slice.currency,
            extra_metadata={
                "list_cost": str(ps.slice.list_cost),
                "credit": str(ps.slice.credit),
                "quantity": str(ps.slice.quantity),
                "prior_period": "true" if ps.slice.meta.get("prior_period") else "false",
            },
        ))
    for step in residual_trace:
        item_no += 1
        kind = "min_charge" if step.delta > 0 else "cap"
        session.add(PricingRunItem(
            run_id=run.id, org_path=run.org_path, item_number=item_no,
            group_key=f"_invoice.{kind}", group_label=step.rule_code,
            line_kind=kind, source_record_ids=[],
            rule_version_ids=[step.rule_version_id],
            input_amount=q(step.input_amount, mode=rounding),
            output_amount=q(step.delta, mode=rounding),
            provider_cost_amount=ZERO,
            formula=step.formula,
            calculation_trace=[{"rule": step.rule_code, "v": step.version_number,
                                "action": step.action, "input": str(step.input_amount),
                                "output": str(step.output_amount), "delta": str(step.delta),
                                "formula": step.formula}],
            currency="USD",
        ))
    # provider-side extras (support/tax) become priced items at cost so the
    # invoice builder can add them per contract policy
    if support_cost != 0 or tax_cost != 0:
        for kind, amount in (("support", support_cost), ("tax", tax_cost)):
            if amount == 0:
                continue
            item_no += 1
            session.add(PricingRunItem(
                run_id=run.id, org_path=run.org_path, item_number=item_no,
                group_key=f"_{kind}", group_label=f"Provider {kind}", line_kind=kind,
                source_record_ids=[], rule_version_ids=[],
                input_amount=q(amount, mode=rounding),
                output_amount=q(amount, mode=rounding),
                provider_cost_amount=q(amount, mode=rounding),
                formula=f"provider {kind} total (policy applied at invoice build)",
                calculation_trace=[], currency="USD",
            ))
    return totals
