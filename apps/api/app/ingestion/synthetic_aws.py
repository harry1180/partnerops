"""Deterministic synthetic AWS-style billing generator (Phase 1 fixtures).

Clean-room note: this is OUR OWN interchange format (snake_case columns), not
a reproduction of any provider's export schema. A real AWS CUR adapter will
parse AWS's actual format when credentials exist; until then this generator
provides the demo/development dataset with every edge case the charter names.

Seed: fixed → byte-identical CSVs on every run. All amounts are generated in
integer cents and rendered as decimal strings.

Required scenarios produced (see docs/demo-dataset.md):
- 3 billing months: 2026-06, 2026-07, 2026-08 (+ Sept late adjustments)
- compute/storage/database/network/marketplace/support/tax
- RI discount rows (usage shows provider_billed < ondemand_equivalent)
- service credits + one refund
- a duplicate source row inside a file
- an unmapped linked account (999999999999)
- negative-margin customer (Cobalt: billed below cost)
- expiring contract (Cobalt)
- a major cost anomaly (Cobalt network spike Aug)
- provider bill total that differs from the line sum by $37.50 (Aug) →
  reconciliation exception

Outputs per payer/month: fixtures/aws/<payer>/<period>.csv
"""

from __future__ import annotations

import csv
import io
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SEED = 20260916
PERIODS = [
    (datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "2026-06"),
    (datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC), "2026-07"),
    (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC), "2026-08"),
]

# Payer + linked accounts (aligned with app/seed.py demo org ids)
NORTHWIND_PAYER = "777700000001"
ACCOUNTS = {
    # linked account → (customer code, environment)
    "111111111111": ("ACME", "prod"),
    "222222222222": ("ACME", "dev"),
    "333333333333": ("BLUR", "prod"),
    "444444444444": ("COBA", "prod"),   # negative-margin customer
    "999999999999": (None, None),        # unassigned / forgotten account
}

# account → (service, usage_type, unit, hourly/GB rate cents, monthly_qty)
SERVICES = [
    ("Amazon Elastic Compute Cloud", "BoxUsage:m5.xlarge", "Hrs", "EC2Compute", 192, 720),
    ("Amazon Elastic Compute Cloud", "BoxUsage:c5.2xlarge", "Hrs", "EC2Compute", 340, 360),
    ("Amazon Simple Storage Service", "TimedStorage-ByteHrs", "GB-Mo", "Storage", 23, 15000),
    ("Amazon Relational Database Service", "InstanceUsage:db.r5.large", "Hrs", "RDSDatabase", 262, 720),
    ("Amazon CloudFront", "Requests-Tier1", "Requests", "Network", 1, 900000),
    ("Amazon CloudFront", "Bytes Transfer-To-Internet", "GB", "Network", 120, 1800),
    ("AWS Support (Business)", "Support", "Monthly", "Support", 0, 0),  # computed
    ("AWS Marketplace", "Software(Redis-Labs)", "Hrs", "Marketplace", 250, 720),
]

RI_APPLIED_SERVICES = {"EC2Compute", "RDSDatabase"}
TAG_SETS = {
    "111111111111": ("payments-api", "prod", "Priya Kumar", "CC-1001"),
    "222222222222": ("payments-api", "dev", "Priya Kumar", "CC-1001"),
    "333333333333": ("data-platform", "prod", "Luis Ortega", "CC-2002"),
    "444444444444": ("ml-research", "prod", "Hana Sato", "CC-3003"),
    "999999999999": ("legacy-batch", "prod", None, None),
}

HEADER = [
    "billing_period_start", "billing_period_end", "usage_start", "usage_end",
    "line_item_id", "invoice_id", "line_item_type", "cost_category",
    "payer_account_id", "linked_account_id", "resource_id", "service",
    "sku", "usage_type", "operation", "region", "availability_zone",
    "quantity", "unit", "currency", "public_ondemand_unit_price",
    "list_cost", "ondemand_cost_equivalent", "unblended_cost",
    "amortized_cost", "effective_cost", "net_cost", "credit_amount",
    "tax_amount", "support_fee_amount", "marketplace_fee_amount",
    "tag_application", "tag_environment", "tag_owner", "tag_cost_center",
    "is_late_adjustment",
]


def cents(c: int) -> str:
    return f"{Decimal(c) / 100:.6f}"


def gen_month(rng: random.Random, payer: str, period_label: str,
              p_start: datetime, p_end: datetime) -> list[dict]:
    rows: list[dict] = []
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"{payer}-{period_label}-{seq:06d}"

    usage_mid = p_start.replace(day=15, hour=12)
    for acct in ACCOUNTS:
        app_, env_, owner_, cc_ = TAG_SETS[acct]
        for (service, usage_type, unit, category, rate_c, qty) in SERVICES:
            # deterministic jitter ±12%
            jitter = rng.randint(88, 112)
            if category == "Support":
                continue  # emitted once per account below
            q = qty * jitter // 100
            gross_c = rate_c * q
            if acct == "444444444444" and category == "Network" and "Tier1" in usage_type \
                    and period_label == "2026-08":
                q *= 14  # THE cost anomaly: 14× CloudFront requests for Cobalt
                gross_c = rate_c * q
            if acct == "444444444444" and category == "EC2Compute" and period_label == "2026-06":
                q = qty  # keep baseline; negative margin comes from rate policy
            provider_c = gross_c
            ri_note = ""
            if category in RI_APPLIED_SERVICES:
                # 35% RI-style discount on provider cost
                provider_c = gross_c * 65 // 100
                ri_note = "ri-covered"
            row = {
                "billing_period_start": p_start.isoformat(),
                "billing_period_end": p_end.isoformat(),
                "usage_start": (p_start if period_label != "2026-08" else usage_mid).isoformat(),
                "usage_end": p_end.isoformat(),
                "line_item_id": next_id(),
                "invoice_id": f"AWS-{period_label}-{payer[-4:]}",
                "line_item_type": "usage",
                "cost_category": category,
                "payer_account_id": payer,
                "linked_account_id": acct,
                "resource_id": f"{category.lower()}-{acct[-6:]}-{usage_type.split(':')[0]}",
                "service": service,
                "sku": f"SKU-{category[:3].upper()}-{usage_type[-6:]}",
                "usage_type": usage_type,
                "operation": "RunInstances" if category == "EC2Compute" else "Put",
                "region": "us-east-1",
                "availability_zone": "us-east-1a",
                "quantity": f"{q}",
                "unit": unit,
                "currency": "USD",
                "public_ondemand_unit_price": cents(rate_c),
                "list_cost": cents(gross_c),
                "ondemand_cost_equivalent": cents(gross_c),
                "unblended_cost": cents(provider_c),
                "amortized_cost": cents(provider_c),
                "effective_cost": cents(provider_c),
                "net_cost": cents(provider_c),
                "credit_amount": "0.000000",
                "tax_amount": "0.000000",
                "support_fee_amount": "0.000000",
                "marketplace_fee_amount": cents(gross_c) if category == "Marketplace" else "0.000000",
                "tag_application": app_,
                "tag_environment": env_,
                "tag_owner": owner_ or "",
                "tag_cost_center": cc_ or "",
                "is_late_adjustment": "false",
            }
            rows.append(row)
            if ri_note:
                # an offsetting RI recurring row (negative amortized delta bookkeeping)
                rows.append({**row,
                             "line_item_id": next_id(),
                             "line_item_type": "savings_plan_recurring",
                             "unblended_cost": cents(gross_c - provider_c),
                             "amortized_cost": cents(-(gross_c - provider_c)),
                             "net_cost": "0.000000",
                             "effective_cost": "0.000000",
                             "quantity": "0",
                             "service": "Savings Plans",
                             "cost_category": "Commitment",
                             "sku": "SP-RECURRING",
                             "usage_type": "SavingsPlanRecurring"})
        # support fee = 10% of this account's month usage, rounded to cents
        acct_total = sum(int(Decimal(r["unblended_cost"]) * 100) for r in rows
                         if r["linked_account_id"] == acct and r["line_item_type"] == "usage")
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "support",
                     "cost_category": "Support",
                     "service": "AWS Support (Business)",
                     "sku": "SUPPORT-BIZ",
                     "usage_type": "Support",
                     "operation": "Support",
                     "quantity": "1",
                     "unit": "Monthly",
                     "resource_id": f"support-{acct}",
                     "list_cost": cents(acct_total // 10),
                     "ondemand_cost_equivalent": cents(acct_total // 10),
                     "unblended_cost": cents(acct_total // 10),
                     "amortized_cost": cents(acct_total // 10),
                     "effective_cost": cents(acct_total // 10),
                     "net_cost": cents(acct_total // 10),
                     "public_ondemand_unit_price": "0.000000",
                     "support_fee_amount": cents(acct_total // 10),
                     "credit_amount": "0.000000",
                     "marketplace_fee_amount": "0.000000"})
        # tax = 8% of usage for this month's rows
    # monthly totals for tax lines
    total_c = sum(int(Decimal(r["unblended_cost"]) * 100) for r in rows
                  if r["line_item_type"] in ("usage", "support"))
    rows.append({**rows[-1],
                 "line_item_id": next_id(),
                 "line_item_type": "tax",
                 "cost_category": "Tax",
                 "service": "Tax",
                 "sku": "TAX-US",
                 "usage_type": "Tax",
                 "resource_id": "tax-monthly",
                 "quantity": "1", "unit": "Monthly",
                 "list_cost": cents(total_c * 8 // 100),
                 "ondemand_cost_equivalent": "0.000000",
                 "unblended_cost": cents(total_c * 8 // 100),
                 "amortized_cost": cents(total_c * 8 // 100),
                 "effective_cost": cents(total_c * 8 // 100),
                 "net_cost": "0.000000",
                 "public_ondemand_unit_price": "0.000000",
                 "tax_amount": cents(total_c * 8 // 100),
                 "support_fee_amount": "0.000000",
                 "marketplace_fee_amount": "0.000000",
                 "credit_amount": "0.000000"})
    # credits
    if period_label == "2026-07":
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "credit",
                     "cost_category": "Credit",
                     "service": "AWS Credit",
                     "sku": "CREDIT-SUPPORT-GOODWILL",
                     "usage_type": "Credit",
                     "unblended_cost": "-40000",
                     "amortized_cost": "-40000",
                     "effective_cost": "-40000",
                     "net_cost": "-40000",
                     "credit_amount": "-40000",
                     "quantity": "1", "unit": "USD",
                     "list_cost": "-40000",
                     "ondemand_cost_equivalent": "0.000000",
                     "public_ondemand_unit_price": "0.000000",
                     "tax_amount": "0.000000",
                     "support_fee_amount": "0.000000",
                     "marketplace_fee_amount": "0.000000",
                     "resource_id": "credit-july"})
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "refund",
                     "cost_category": "Credit",
                     "service": "AWS Refund",
                     "sku": "REFUND-MARKETPLACE",
                     "usage_type": "Refund",
                     "unblended_cost": "-12000",
                     "amortized_cost": "-12000",
                     "effective_cost": "-12000",
                     "net_cost": "-12000",
                     "credit_amount": "-12000",
                     "list_cost": "-12000",
                     "resource_id": "refund-july",
                     "marketplace_fee_amount": "0.000000"})
    return rows


def build_all() -> dict[str, dict[str, str]]:
    """returns payer → period_label → csv text"""
    rng = random.Random(SEED)
    out: dict[str, dict[str, str]] = {NORTHWIND_PAYER: {}}
    for p_start, p_end, label in PERIODS:
        rows = gen_month(rng, NORTHWIND_PAYER, label, p_start, p_end)
        # a duplicate source record: re-append an existing usage row verbatim
        if label == "2026-07":
            dup = dict(rows[3])
            rows.append(dup)
        # late-arriving adjustments in the Aug file: June usage corrected
        if label == "2026-08":
            late = {**rows[0],
                    "line_item_id": f"{NORTHWIND_PAYER}-2026-08-LATE-000001",
                    "billing_period_start": PERIODS[0][0].isoformat(),
                    "billing_period_end": PERIODS[0][1].isoformat(),
                    "usage_start": PERIODS[0][0].replace(day=11).isoformat(),
                    "line_item_type": "adjustment",
                    "cost_category": "Adjustment",
                    "unblended_cost": "4500",
                    "amortized_cost": "4500",
                    "effective_cost": "4500",
                    "net_cost": "4500",
                    "quantity": "1",
                    "is_late_adjustment": "true",
                    "resource_id": "late-june-11"}
            rows.append(late)
        # August: the authoritative provider invoice total differs from the
        # line sum by +4500.00 (off-line true-up) → reconciliation
        # discrepancy scenario. Emitted last so it covers every other row.
        if label == "2026-08":
            line_total_c = sum(int(Decimal(r["unblended_cost"]) * 100) for r in rows)
            rows.append({**rows[-1],
                         "line_item_id": f"{NORTHWIND_PAYER}-2026-08-SUMMARY-000001",
                         "usage_start": p_start.isoformat(),
                         "usage_end": p_end.isoformat(),
                         "is_late_adjustment": "false",
                         "line_item_type": "provider_summary",
                         "cost_category": "Summary",
                         "service": "AWS Invoice Summary",
                         "sku": "SUMMARY",
                         "usage_type": "InvoiceTotal",
                         "operation": "Summary",
                         "resource_id": "invoice-summary",
                         "quantity": "0",
                         "unit": "Monthly",
                         "list_cost": "0.000000",
                         "ondemand_cost_equivalent": "0.000000",
                         "unblended_cost": cents(line_total_c + 450000),
                         "amortized_cost": "0.000000",
                         "effective_cost": "0.000000",
                         "net_cost": "0.000000",
                         "credit_amount": "0.000000",
                         "tax_amount": "0.000000",
                         "support_fee_amount": "0.000000",
                         "marketplace_fee_amount": "0.000000",
                         "tag_application": "",
                         "tag_environment": "",
                         "tag_owner": "",
                         "tag_cost_center": ""})
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        out[NORTHWIND_PAYER][label] = buf.getvalue()
    return out


def write_fixtures(root: Path) -> list[Path]:
    written: list[Path] = []
    data = build_all()
    for payer, months in data.items():
        d = root / "aws" / payer
        d.mkdir(parents=True, exist_ok=True)
        for label, text in months.items():
            path = d / f"{label}.csv"
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


if __name__ == "__main__":
    import sys
    repo_root = Path(__file__).resolve().parents[3].parent  # apps/api/app/ingestion → repo root
    target = repo_root / "fixtures"
    files = write_fixtures(target)
    for f in files:
        print("wrote", f, len(f.read_text(encoding='utf-8')), "bytes")
    sys.exit(0)
