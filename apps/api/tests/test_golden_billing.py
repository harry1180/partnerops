"""Golden billing fixtures: known inputs → known customer charges, partner
cost, and margin. If these numbers move, something in the engine changed —
that must be a deliberate, reviewed decision (docs/billing-engine.md)."""

from decimal import Decimal

from app.engine.money import ZERO, q
from app.engine.rules import CostSlice, EffectiveRule, apply_rules, invoice_level_adjustments

D = Decimal


def slice_of(label: str, billed: str, credit: str = "0") -> CostSlice:
    return CostSlice(
        key=label, label=label, currency="USD",
        list_cost=D(billed), ondemand_equivalent=D(billed),
        provider_billed=D(billed), amortized=D(billed), effective=D(billed),
        net=D(billed) + D(credit), credit=D(credit),
        tax=ZERO, support_fee=ZERO, marketplace_fee=ZERO,
        quantity=D("1"), rule_targets=frozenset({label}),
    )


GOLDEN_MS_ADMIN = [
    # Northwind MSP standard: +12% markup on provider billed, support +10%
    # pass-through, credits retained by partner (100%).
    EffectiveRule("r1", "v1", "R-MARKUP", "Standard markup", "percentage_markup", 10, 10,
                  "running_total", {}, {"type": "percentage_markup", "percent": "12"}, 1),
    EffectiveRule("r2", "v2", "R-CREDIT", "Credit retention", "credit_retention", 50, 50,
                  "running_total", {}, {"type": "credit_retention", "percent": "100"}, 1),
    EffectiveRule("r3", "v3", "R-MSF", "Min monthly", "minimum_monthly", 90, 90,
                  "running_total", {}, {"type": "minimum_monthly", "amount": "100.00"}, 1),
]


def test_golden_ms_admin_markup_and_margin():
    provider = D("4126.50")  # arbitrary but fixed provider cost
    priced = apply_rules([slice_of("compute", str(provider))], GOLDEN_MS_ADMIN, "provider_billed")
    expected_customer = q(provider * D("1.12"))  # 4621.68
    assert priced[0].customer_amount == expected_customer
    trace = invoice_level_adjustments(priced, GOLDEN_MS_ADMIN)
    assert trace == []  # above minimum
    total = expected_customer
    margin = total - provider
    assert margin == D("495.18")  # exactly 12% of provider cost


def test_golden_minimum_monthly_binds():
    priced = apply_rules([slice_of("tiny", "50.00")], GOLDEN_MS_ADMIN, "provider_billed")
    # 50 × 1.12 = 56 < 100 min → residual +44
    residuals = invoice_level_adjustments(priced, GOLDEN_MS_ADMIN)
    assert len(residuals) == 1
    assert residuals[0].delta == D("44.000000")
    total = q(priced[0].customer_amount + residuals[0].delta)
    assert total == D("100.00")
    # 50 cost → 100 billed = 100% margin (the minimum rescued a low-usage
    # month); the leakage signal this guards is the reverse case.
    margin = total - D("50.00")
    assert margin == D("50.00")


def test_golden_credit_retention_keeps_credit():
    provider = D("1000.00")
    priced = apply_rules([slice_of("s3", str(provider), credit="-300.00")],
                         GOLDEN_MS_ADMIN, "provider_billed")
    # +12% then full credit retention → customer billed 1120, partner net 700
    assert priced[0].customer_amount == D("1120.00")
    partner_economics = provider - D("300.00")
    margin = priced[0].customer_amount - partner_economics
    assert margin == D("420.00")


def test_golden_credit_passthrough_customer_gets_benefit():
    provider = D("1000.00")
    rules = [
        EffectiveRule("r1", "v1", "R-MARKUP", "m", "percentage_markup", 10, 10,
                      "running_total", {}, {"type": "percentage_markup", "percent": "12"}, 1),
        EffectiveRule("r2", "v2", "R-PT", "pass 50% of credits", "credit_pass_through",
                      50, 50, "running_total", {},
                      {"type": "credit_pass_through", "percent": "50"}, 1),
    ]
    priced = apply_rules([slice_of("s3", str(provider), credit="-300.00")], rules, "provider_billed")
    # 1000×1.12 = 1120 ; pass -150 → 970
    assert priced[0].customer_amount == D("970.00")
    # partner pays 700 (1000 − 300 credit) and bills 970 → margin 270
    assert priced[0].customer_amount - (provider - D("300")) == D("270.00")


def test_golden_support_remove_policy_strips_fee():
    rules = [EffectiveRule("r", "v", "R-SUP", "remove support", "support_charge", 20, 20,
                           "running_total", {}, {"type": "support_charge", "mode": "remove"}, 1)]
    s = slice_of("all", "1000.00")
    s.support_fee = D("100.00")
    priced = apply_rules([s], rules, "provider_billed")
    assert priced[0].customer_amount == D("900.00")  # support stripped
