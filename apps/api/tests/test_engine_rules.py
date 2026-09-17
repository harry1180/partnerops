"""Billing-engine unit + property tests (charter: test calculations rigorously)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

from app.engine.money import ZERO, q
from app.engine.rules import (
    CostSlice,
    EffectiveRule,
    apply_rules,
    invoice_level_adjustments,
    sort_rules,
)

D = Decimal


def s(**kw) -> CostSlice:  # type: ignore[no-untyped-def]
    base = {
        "key": "k", "label": "L", "currency": "USD",
        "list_cost": D("1000"), "ondemand_equivalent": D("1000"),
        "provider_billed": D("1000"), "amortized": D("1000"),
        "effective": D("1000"), "net": D("1000"),
        "credit": ZERO, "tax": D("80"), "support_fee": D("10"), "marketplace_fee": ZERO,
        "quantity": D("100"),
        "rule_targets": frozenset({"Amazon Elastic Compute Cloud", "SKU-EC", "EC2Compute", "us-east-1"}),
    }
    base.update(kw)
    return CostSlice(**base)


def r(code, type_, priority=100, calc=100, basis="running_total", filters=None, params=None, vid=None):  # type: ignore[no-untyped-def]
    return EffectiveRule(
        rule_id=vid or code, version_id=vid or code, code=code, name=code,
        rule_type=type_, priority=priority, calc_order=calc,
        applied_basis=basis, filters=filters or {}, parameters=params or {},
    )


def test_percentage_markup_on_provider_billed():
    out = apply_rules([s()], [r("mk", "percentage_markup", params={"percent": "12.5"})], "provider_billed")
    assert out[0].customer_amount == D("1125.0")
    assert "12.5%" in out[0].trace[0].formula


def test_priority_order_changes_result():
    # percent and fixed additive rules are order-sensitive:
    # (+100 then +10%) = 1210 vs (+10% then +100) = 1200
    mk = r("mk", "percentage_markup", priority=1, params={"percent": "10"})
    fx = r("fx", "fixed_recurring", priority=2, params={"amount": "100"})
    a = apply_rules([s()], [mk, fx], "provider_billed")[0].customer_amount
    mk2 = r("mk", "percentage_markup", priority=1, params={"percent": "10"})
    fx2 = r("fx", "fixed_recurring", priority=0, params={"amount": "100"})
    b = apply_rules([s()], [fx2, mk2], "provider_billed")[0].customer_amount
    assert a == D("1200.0")  # 1100 + 100
    assert b == D("1210.0")  # (1000+100) × 1.1


def test_tier_boundaries_exact():
    # tiers: first 100 units @ 1.00, next 400 @ 0.50, rest @ 0.20
    tiers = {"tiers": [{"up_to": 100, "rate": "1.00"}, {"up_to": 400, "rate": "0.50"},
                       {"up_to": None, "rate": "0.20"}]}
    for qty, expected in (("100", "100.000000"), ("101", "100.500000"),
                          ("500", "300.000000"), ("501", "300.200000"), ("0", "0")):
        out = apply_rules([s(quantity=D(qty), key="t", list_cost=D(qty), provider_billed=D(qty))],
                          [r("tr", "tiered", params=tiers)], "provider_billed")
        assert float(out[0].customer_amount) == pytest.approx(float(D(expected)), abs=1e-9)


def test_minimum_monthly_adds_explicit_residual():
    slices = [s(provider_billed=D("400"), list_cost=D("400"), ondemand_equivalent=D("400"),
                effective=D("400"), net=D("400"), amortized=D("400"))]
    priced = apply_rules(slices, [r("base", "percentage_markup", params={"percent": "0"})], "provider_billed")
    trace = invoice_level_adjustments(priced, [r("min", "minimum_monthly", params={"amount": "500.00"})])
    assert len(trace) == 1
    assert trace[0].delta == D("100.0")
    assert trace[0].formula.startswith("total 400")


def test_maximum_cap_reduces_total():
    priced = apply_rules([s()], [], "provider_billed")
    trace = invoice_level_adjustments(priced, [r("cap", "maximum_cap", params={"amount": "950.00"})])
    assert trace and trace[0].delta == D("-50.0")


def test_credit_retention_full_keeps_customer_at_provider_billed():
    cs = s(credit=D("-200"), net=D("800"), provider_billed=D("1000"))
    out = apply_rules([cs], [r("cr", "credit_retention", params={"percent": "100"})], "provider_billed")
    assert out[0].customer_amount == D("1000.0")  # no credit passed


def test_credit_pass_through_partial_splits():
    cs = s(credit=D("-200"), net=D("800"))
    out = apply_rules([cs], [r("cp", "credit_pass_through", params={"percent": "50"})], "provider_billed")
    assert out[0].customer_amount == D("900.0")  # half of -200 passed through


def test_negative_manual_adjustment_and_floor():
    out = apply_rules([s()], [r("adj", "manual_adjustment", params={"amount": "-1500"})], "provider_billed")
    assert out[0].customer_amount == D("-500.0")  # negatives allowed (credits/refunds flow)


def test_filters_match_and_miss():
    ec2 = s()
    s3 = s(key="s3", rule_targets=frozenset({"Amazon Simple Storage Service", "SKU-S3"}))
    rule = r("only-ec2", "percentage_markup", filters={"service": ["Amazon Elastic Compute Cloud"]},
             params={"percent": "10"})
    priced = apply_rules([ec2, s3], [rule], "provider_billed")
    assert priced[0].customer_amount == D("1100.0")
    assert priced[1].customer_amount == D("1000.0")
    assert len(priced[1].trace) == 0


def test_applied_basis_original_ignores_running_total():
    a = r("a", "percentage_markup", priority=1, params={"percent": "50"})
    b = r("b", "percentage_markup", priority=2, basis="original_input", params={"percent": "50"})
    # b adds 50% of the ORIGINAL contract basis (1000) = +500 on top of 1500
    out = apply_rules([s()], [a, b], "provider_billed")
    assert out[0].customer_amount == D("2000.0")


def test_unknown_rule_type_fails_closed():
    with pytest.raises(ValueError):
        apply_rules([s()], [r("x", "quantum_discount")], "provider_billed")


def test_sort_rules_deterministic_tie_break():
    r1 = r("z-rule", "percentage_markup", priority=5, calc=5, vid="1")
    r2 = r("a-rule", "percentage_markup", priority=5, calc=5, vid="2")
    assert [x.code for x in sort_rules([r1, r2])] == ["z-rule", "a-rule"]  # by rule_id tie-break


# ---------------- hypothesis property tests ----------------

money = st.decimals(min_value=Decimal("-50000"), max_value=Decimal("50000"),
                    places=6, allow_nan=False, allow_infinity=False)
pcts = st.decimals(min_value=Decimal("-100"), max_value=Decimal("500"),
                   places=4, allow_nan=False, allow_infinity=False)


@hsettings(max_examples=200, deadline=None)
@given(billed=money, pct=pcts)
def test_markup_then_equal_discount_is_identity(billed, pct):
    """±x% then ∓x% are not exact inverses, but +x% then -x% never drifts
    beyond rounding: assert bounded error, and 0% markup is identity."""
    slices = [s(provider_billed=billed, list_cost=billed, ondemand_equivalent=billed,
                amortized=billed, effective=billed, net=billed)]
    chained = apply_rules(
        slices,
        [r("u", "percentage_markup", priority=1, params={"percent": str(pct)}),
         r("d", "percentage_discount", priority=2, params={"percent": str(pct)})],
        "provider_billed")[0]
    # billed × (1+p)(1−p) = billed(1−p²); plus ≤ 2 engine quantizations (1e-6)
    bound = abs(billed) * (abs(pct) / 100) ** 2 + D("0.000003")
    assert abs(chained.customer_amount - billed) <= bound
    zero = apply_rules(slices, [r("z", "percentage_markup", params={"percent": "0"})], "provider_billed")[0]
    assert abs(zero.customer_amount - billed) <= D("0.000001")


@hsettings(max_examples=150, deadline=None)
@given(billed=money, credit=money)
def test_credit_pass_through_bounded(billed, credit):
    """Passing p% of a credit never moves the customer amount further from
    billed than the full credit, and a zero percent is identity."""
    if credit > 0:
        credit = -credit
    slices = [s(provider_billed=billed, credit=credit, net=billed + credit)]
    full = apply_rules(slices, [r("c", "credit_pass_through", params={"percent": "100"})], "provider_billed")[0]
    none_ = apply_rules(slices, [r("c", "credit_pass_through", params={"percent": "0"})], "provider_billed")[0]
    assert none_.customer_amount == billed or abs(none_.customer_amount - billed) <= D("0.000002")
    if credit < 0:
        assert full.customer_amount <= billed + D("0.000002")


@hsettings(max_examples=120, deadline=None)
@given(billed=money, floor=money)
def test_minimum_never_below_floor(billed, floor):
    priced = apply_rules([s(provider_billed=billed, list_cost=billed, ondemand_equivalent=billed,
                            amortized=billed, effective=billed, net=billed)], [], "provider_billed")
    inv_trace = invoice_level_adjustments(priced, [r("m", "minimum_monthly", params={"amount": str(floor)})])
    total = billed + sum((step.delta for step in inv_trace), ZERO)
    # settlement semantics: the minimum is a contract money amount quantized
    # to invoice scale (2dp), exactly as the engine applies it.
    floor_q = q(floor)
    if billed > 0:
        assert total >= floor_q - D("0.000002")
    else:
        # documented semantics: a minimum is never charged on a zero/negative
        # usage month — that case must flow to credit notes, not a floor charge
        assert not inv_trace
