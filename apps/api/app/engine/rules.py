"""Deterministic billing rule engine (pure — no DB, no clock).

Input:  list of CostSlice (pre-grouped canonical amounts) + ordered
        EffectiveRule list + contract policy numbers.
Output: per-slice priced amounts + calculation trace, plus invoice-level
        residuals (minimum charge top-up, cap reduction).

Every number is Decimal; engine scale 6dp intermediate, invoice scale at the
final quantize (mode from contract rounding rule). Every rule application
records a human-readable formula string — the lineage the charter demands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.engine.money import ZERO, engine, pct_of, q

APPLIED_RUNNING = "running_total"
APPLIED_ORIGINAL = "original_input"


@dataclass
class CostSlice:
    """One grouping key's aggregated provider economics for a period."""

    key: str                      # e.g. "service:Amazon Elastic Compute Cloud"
    label: str
    currency: str
    list_cost: Decimal
    ondemand_equivalent: Decimal
    provider_billed: Decimal      # what provider charges partner (usage lines)
    amortized: Decimal
    effective: Decimal
    net: Decimal                  # provider_billed + credits (credits negative)
    credit: Decimal               # negative when provider granted credits
    tax: Decimal
    support_fee: Decimal
    marketplace_fee: Decimal
    quantity: Decimal = ZERO
    source_record_ids: tuple[str, ...] = ()
    rule_targets: frozenset[str] = frozenset()  # services/skus etc for matching
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EffectiveRule:
    """A rule version frozen into a pricing run."""

    rule_id: str
    version_id: str
    code: str
    name: str
    rule_type: str
    priority: int
    calc_order: int
    applied_basis: str
    filters: dict            # {"service": [...], "sku": [...], "cost_category": [...], ...}
    parameters: dict         # {"percent": "12.5", "amount": "350.00", ...}
    version_number: int = 1


@dataclass
class TraceStep:
    rule_code: str
    rule_version_id: str
    version_number: int
    action: str          # applied | no-match | skipped
    formula: str
    input_amount: Decimal
    output_amount: Decimal
    delta: Decimal


@dataclass
class PricedSlice:
    slice: CostSlice
    basis_amount: Decimal
    customer_amount: Decimal
    trace: list[TraceStep] = field(default_factory=list)
    applied_rule_ids: list[str] = field(default_factory=list)


def rule_matches(rule: EffectiveRule, s: CostSlice) -> bool:
    f = rule.filters or {}
    def _list(key: str, values) -> bool:
        if not values:
            return True
        return set(values) & set(s.rule_targets) != set()

    if "service" in f and not _list("service", f["service"]):
        return False
    if "sku" in f and not _list("sku", f["sku"]):
        return False
    if "cost_category" in f and not _list("cost_category", f["cost_category"]):
        return False
    if "usage_type" in f and not _list("usage_type", f["usage_type"]):
        return False
    if "region" in f and not _list("region", f["region"]):
        return False
    if "account" in f and s.meta.get("linked_account") not in set(f["account"]):
        return False
    if "tag" in f:  # {"application": ["payments-api"]} style
        for tag_key, tag_vals in f["tag"].items():
            if s.meta.get("tags", {}).get(tag_key) not in set(tag_vals):
                return False
    return True


def basis_amount(rule: EffectiveRule, s: CostSlice, running: Decimal, contract_basis: str) -> Decimal:
    if rule.applied_basis == APPLIED_ORIGINAL:
        return _contract_basis(s, contract_basis)
    return running


def _contract_basis(s: CostSlice, contract_basis: str) -> Decimal:
    return {
        "provider_billed": s.provider_billed,
        "list": s.list_cost,
        "ondemand_equivalent": s.ondemand_equivalent,
        "net_after_credits": s.net,
    }.get(contract_basis, s.provider_billed)


def _num(parameters: dict, key: str, default: Decimal = ZERO) -> Decimal:
    raw = parameters.get(key)
    if raw is None:
        return default
    if isinstance(raw, float):
        raise TypeError("rule parameters must carry Decimal/str amounts, never float")
    return Decimal(str(raw))


def sort_rules(rules: list[EffectiveRule]) -> list[EffectiveRule]:
    # deterministic: priority → calc_order → rule_id (tie-break stable)
    return sorted(rules, key=lambda r: (r.priority, r.calc_order, r.rule_id))


def apply_rules(
    slices: list[CostSlice],
    rules: list[EffectiveRule],
    contract_basis: str,
) -> list[PricedSlice]:
    ordered = sort_rules(rules)
    priced: list[PricedSlice] = []
    for s in slices:
        running = _contract_basis(s, contract_basis)
        ps = PricedSlice(slice=s, basis_amount=running, customer_amount=running)
        for rule in ordered:
            if not rule_matches(rule, s):
                continue
            input_amount = basis_amount(rule, s, running, contract_basis)
            delta, absolute, formula = _apply_one(rule, s, input_amount, running)
            if absolute is not None:
                new_amount = absolute
                delta = new_amount - running
            else:
                new_amount = engine(running + delta)
            ps.trace.append(TraceStep(
                rule_code=rule.code, rule_version_id=rule.version_id,
                version_number=rule.version_number, action="applied",
                formula=formula, input_amount=engine(input_amount),
                output_amount=engine(new_amount), delta=engine(delta),
            ))
            ps.applied_rule_ids.append(rule.rule_id)
            running = new_amount
        ps.customer_amount = running
        priced.append(ps)
    return priced


def _apply_one(
    rule: EffectiveRule, s: CostSlice, basis: Decimal, running: Decimal
) -> tuple[Decimal, Decimal | None, str]:
    """Return (delta, absolute_replacement_or_None, formula).

    Additive rules contribute a delta computed on `basis` (running_total or
    contract basis, per rule.applied_basis) — so stacking a rule applied to
    the original input composes instead of clobbering. Replacement rules
    (fixed_unit_rate, tiered, data_exclusion) set the amount directly.
    """
    t_ = rule.rule_type
    p = rule.parameters or {}
    if t_ == "percentage_markup":
        pct = _num(p, "percent")
        delta = pct_of(basis, pct)
        return delta, None, f"markup {pct}% of {basis:.6f} = +{delta:.6f} (running {running:.6f})"
    if t_ == "percentage_discount":
        pct = _num(p, "percent")
        delta = -pct_of(basis, pct)
        return delta, None, f"discount {pct}% of {basis:.6f} = {delta:.6f}"
    if t_ == "fixed_unit_rate":
        rate = _num(p, "rate")
        total = engine(rate * s.quantity)
        return total - running, total, f"rate {rate} × {s.quantity} = {total:.6f} (replaces)"
    if t_ == "fixed_recurring":
        amount = _num(p, "amount")
        if _num(p, "replaces", ZERO) == Decimal("1"):
            return amount - running, amount, f"replaced with fixed {amount}"
        return amount, None, f"+ fixed recurring {amount}"
    if t_ == "one_time":
        amount = _num(p, "amount")
        return amount, None, f"+ {amount} (one-time)"
    if t_ == "managed_service_fee":
        pct = _num(p, "percent", Decimal("10"))
        flat = _num(p, "flat", ZERO)
        fee = engine(pct_of(basis, pct) + flat)
        return fee, None, f"MSF {pct}% (+{flat}) of {basis:.6f} = +{fee:.6f}"
    if t_ == "tax_adjustment":
        pct = _num(p, "percent")
        delta = pct_of(basis, pct)
        return delta, None, f"tax {pct}% of {basis:.6f} = +{delta:.6f}"
    if t_ == "credit_retention":
        if s.credit < ZERO:
            retain_pct = _num(p, "percent", Decimal("100"))
            kept = engine(pct_of(s.credit, retain_pct))      # negative magnitude kept by partner
            passed = engine(s.credit - kept)                 # remainder goes to customer
            return passed, None, (f"credit {s.credit} → retained {kept:.6f}, "
                                  f"passed {passed:.6f}")
        return ZERO, None, "credit_retention: no credits on slice (no-op)"
    if t_ == "credit_pass_through":
        if s.credit < ZERO:
            pct = _num(p, "percent", Decimal("100"))
            passed = engine(pct_of(s.credit, pct))
            return passed, None, f"pass-through {pct}% of credit {s.credit} = {passed:.6f}"
        return ZERO, None, "credit_pass_through: no credits on slice (no-op)"
    if t_ == "data_exclusion":
        return ZERO, ZERO, f"excluded from customer view ({running:.6f} → 0)"
    if t_ == "support_charge":
        mode = str(p.get("mode", "pass_through"))
        if mode == "remove":
            return -s.support_fee, None, f"support removed (-{s.support_fee})"
        if mode == "replace":
            repl = _num(p, "amount")
            return repl - s.support_fee, None, f"support replaced by {repl}"
        return ZERO, None, "support passed through (no-op)"
    if t_ in ("sku_override", "marketplace_adjustment", "custom_service_charge"):
        amount = _num(p, "amount")
        return amount, None, f"{t_} {amount}"
    if t_ == "promotional_credit":
        amount = -abs(_num(p, "amount"))
        return amount, None, f"promo credit {amount}"
    if t_ == "manual_adjustment":
        amount = _num(p, "amount")
        return amount, None, f"manual {amount} (approval required)"
    if t_ == "tiered":
        total = ZERO
        remaining_qty = s.quantity
        last_rate = None
        for tier in p.get("tiers", []):
            up_to = tier.get("up_to")
            rate = Decimal(str(tier["rate"]))
            last_rate = rate
            if up_to is None:
                total += engine(remaining_qty * rate)
                remaining_qty = ZERO
                break
            band = min(remaining_qty, Decimal(str(up_to)))
            total += engine(band * rate)
            remaining_qty -= band
            if remaining_qty <= ZERO:
                break
        if remaining_qty > ZERO and last_rate is not None:
            total += engine(remaining_qty * last_rate)
        return total - running, total, f"tiered: qty {s.quantity} → {total:.6f} (replaces)"
    if t_ in ("minimum_monthly", "maximum_cap"):
        return ZERO, None, f"{t_} handled at invoice level (per-slice no-op)"
    raise ValueError(f"unsupported rule_type {t_!r}")


def invoice_level_adjustments(
    priced: list[PricedSlice],
    rules: list[EffectiveRule],
    rounding_mode: str = "half_up",
) -> list[TraceStep]:
    """Minimum monthly top-up and maximum cap applied to the invoice total.
    Returns trace steps; mutates the last slice's customer_amount so totals
    reflect the residual as an explicit line (the pricing service turns the
    trace steps into line_kind=min_charge/cap rows — nothing silently fudged)."""
    trace: list[TraceStep] = []
    total = sum((p.customer_amount for p in priced), ZERO)
    for rule in sort_rules(rules):
        if rule.rule_type == "minimum_monthly":
            floor = q(_num(rule.parameters, "amount"), mode=rounding_mode)
            # A minimum is a billing floor for a billed month; it never turns
            # a net-negative invoice (credits/refunds exceed usage) positive —
            # that case must flow to a credit note, decided by the caller.
            if total > ZERO and total < floor:
                delta = floor - total
                trace.append(TraceStep(
                    rule_code=rule.code, rule_version_id=rule.version_id,
                    version_number=rule.version_number, action="applied",
                    formula=f"total {total:.6f} < minimum {floor} → +{delta:.6f}",
                    input_amount=engine(total), output_amount=floor, delta=engine(delta),
                ))
                total = floor
        elif rule.rule_type == "maximum_cap":
            ceiling = q(_num(rule.parameters, "amount"), mode=rounding_mode)
            if total > ceiling:
                delta = total - ceiling
                trace.append(TraceStep(
                    rule_code=rule.code, rule_version_id=rule.version_id,
                    version_number=rule.version_number, action="applied",
                    formula=f"total {total:.6f} > cap {ceiling} → -{delta:.6f}",
                    input_amount=engine(total), output_amount=ceiling, delta=engine(-delta),
                ))
                total = ceiling
    return trace
