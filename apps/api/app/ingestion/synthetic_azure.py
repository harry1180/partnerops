"""Deterministic synthetic Azure-style billing generator (Phase 3 fixtures).

Clean-room note: OUR OWN interchange format (snake_case columns, AmortizedCost-
style field NAMES are generic billing vocabulary, but the column set, shapes and
values are ours), not a reproduction of Microsoft's actual export schema. A real
Azure Cost Management adapter parses the live export format when credentials
exist; until then this generator provides the Azure demo dataset with the
charter's required scenarios:

- 3 billing months: 2026-06, 2026-07, 2026-08 (+ Sept late adjustment)
- subscriptions + resource groups (VirtualMachine, Storage, SQL, AppGW,
  Marketplace, support, tax lines)
- reservation discount rows (billed < on-demand equivalent)
- Azure monetary-commitment credit applied + a refund
- a duplicate source row inside a file (2026-07)
- an unmapped subscription (unseen in the console → allocation worklist)
- provider bill total differing from the line sum by $180.00 (2026-08) →
  reconciliation discrepancy
- billing profile (EA-style) as the payer leg

Azure accounts/subscriptions are deliberately distinct UUIDs from AWS so the
same demo customers carry both providers (multi-cloud per customer).

Outputs per billing profile/month: fixtures/azure/<profile>/<period>.csv
"""

from __future__ import annotations

import csv
import io
import random
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

SEED = 20260917
PERIODS = [
    (datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 7, 1, tzinfo=UTC), "2026-06"),
    (datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 8, 1, tzinfo=UTC), "2026-07"),
    (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC), "2026-08"),
]

BILLING_PROFILE = "50001-alpha"  # EA-style billing account (payer leg)

# subscription external id → (customer code, resource group, service line)
SUBSCRIPTIONS = {
    "aaaaaaaa-1111-4111-8111-111111111111": ("ACME", "rg-payments-prod", "prod"),
    "aaaaaaaa-2222-4222-8222-222222222222": ("ACME", "rg-payments-nonprod", "dev"),
    "aaaaaaaa-3333-4333-8333-333333333333": ("BLUR", "rg-data-analytics", "prod"),
    "unmapped-0000-4000-8000-000000000099": (None, "rg-legacy-forgotten", "orphan"),
}

# (service, meter, unit, category, hourly/GB rate cents, monthly_qty, res_covered)
METERS = [
    ("Virtual Machines", "B series standard_B2ms", "Hrs", "Compute", 83, 1460, True),
    ("Virtual Machines", "D series standard_D4s_v5", "Hrs", "Compute", 192, 720, True),
    ("Storage", "Hot LRS Data Stored", "GB-Mo", "Storage", 7, 22000, False),
    ("Azure Database for PostgreSQL", "General Purpose Ds_v2 vCore", "Hrs", "Database", 172, 730, True),
    ("Application Gateway", "V2 Standard Capacity Unit", "Hrs", "Network", 33, 730, False),
    ("Marketplace", "Datadog-SaaS-Logs", "Contracts", "Marketplace", 650, 2, False),
]

TAG_SETS = {
    "aaaaaaaa-1111-4111-8111-111111111111": ("payments-api", "prod", "Priya Kumar", "CC-1001"),
    "aaaaaaaa-2222-4222-8222-222222222222": ("payments-api", "dev", "Priya Kumar", "CC-1001"),
    "aaaaaaaa-3333-4333-8333-333333333333": ("data-platform", "prod", "Luis Ortega", "CC-2002"),
    "unmapped-0000-4000-8000-000000000099": ("legacy-batch", "orphan", "", ""),
}

HEADER = [
    "billing_period_start", "billing_period_end", "usage_start", "usage_end",
    "line_item_id", "invoice_id", "line_item_type", "cost_category",
    "billing_profile_id", "billing_account_name", "subscription_id",
    "subscription_name", "resource_group", "resource_id", "service", "meter",
    "meter_region", "quantity", "unit", "currency", "unit_price",
    "list_cost", "ondemand_cost_equivalent", "pre_tax_cost", "amortized_cost",
    "effective_cost", "net_cost", "credit_cost", "tax_cost", "support_fee_cost",
    "marketplace_cost", "reservation_id", "tag_application", "tag_environment",
    "tag_owner", "tag_cost_center", "is_late_adjustment",
]


def cents(c: int) -> str:
    return f"{Decimal(c) / 100:.6f}"


def gen_month(rng: random.Random, profile: str, period_label: str,
              p_start: datetime, p_end: datetime) -> list[dict]:
    rows: list[dict] = []
    seq = 0

    def next_id() -> str:
        nonlocal seq
        seq += 1
        return f"{profile}-{period_label}-{seq:06d}"

    for sub, (_code, rgroup, env_kind) in SUBSCRIPTIONS.items():
        app_, env_, owner_, cc_ = TAG_SETS[sub]
        sub_name = f"sub-{rgroup.removeprefix('rg-')}"
        for (service, meter, unit, category, rate_c, qty, res_covered) in METERS:
            jitter = rng.randint(90, 110)
            q = qty * jitter // 100
            gross_c = rate_c * q
            billed_c = gross_c * 70 // 100 if (res_covered and env_kind == "prod") else gross_c
            row = {
                "billing_period_start": p_start.isoformat(),
                "billing_period_end": p_end.isoformat(),
                "usage_start": p_start.isoformat(),
                "usage_end": p_end.isoformat(),
                "line_item_id": next_id(),
                "invoice_id": f"AZ-{period_label}-{profile[-5:]}",
                "line_item_type": "usage",
                "cost_category": category,
                "billing_profile_id": profile,
                "billing_account_name": "Northwind EA Enrollment",
                "subscription_id": sub,
                "subscription_name": sub_name,
                "resource_group": rgroup,
                "resource_id": f"/subscriptions/{sub}/resourceGroups/{rgroup}/providers/{service.lower().replace(' ', '')}/{meter.split()[0].lower()}",
                "service": service,
                "meter": meter,
                "meter_region": "eastus2",
                "quantity": f"{q}",
                "unit": unit,
                "currency": "USD",
                "unit_price": cents(rate_c),
                "list_cost": cents(gross_c),
                "ondemand_cost_equivalent": cents(gross_c),
                "pre_tax_cost": cents(billed_c),
                "amortized_cost": cents(billed_c),
                "effective_cost": cents(billed_c),
                "net_cost": cents(billed_c),
                "credit_cost": "0.000000",
                "tax_cost": "0.000000",
                "support_fee_cost": "0.000000",
                "marketplace_cost": cents(gross_c) if category == "Marketplace" else "0.000000",
                "reservation_id": f"ri-{rgroup}" if (res_covered and billed_c < gross_c) else "",
                "tag_application": app_,
                "tag_environment": env_,
                "tag_owner": owner_,
                "tag_cost_center": cc_,
                "is_late_adjustment": "false",
            }
            rows.append(row)
        # support: 5% of this subscription's billed month
        sub_total_c = sum(int(Decimal(r["pre_tax_cost"]) * 100) for r in rows
                          if r["subscription_id"] == sub and r["line_item_type"] == "usage")
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "support",
                     "cost_category": "Support",
                     "service": "Azure Support Plan",
                     "meter": "Support",
                     "quantity": "1",
                     "unit": "Monthly",
                     "resource_id": f"/subscriptions/{sub}/support",
                     "unit_price": cents(sub_total_c * 5 // 100),
                     "list_cost": cents(sub_total_c * 5 // 100),
                     "ondemand_cost_equivalent": "0.000000",
                     "pre_tax_cost": cents(sub_total_c * 5 // 100),
                     "amortized_cost": cents(sub_total_c * 5 // 100),
                     "effective_cost": cents(sub_total_c * 5 // 100),
                     "net_cost": cents(sub_total_c * 5 // 100),
                     "support_fee_cost": cents(sub_total_c * 5 // 100),
                     "credit_cost": "0.000000",
                     "tax_cost": "0.000000",
                     "marketplace_cost": "0.000000",
                     "reservation_id": ""})
    # tax = 8% of billed
    total_c = sum(int(Decimal(r["pre_tax_cost"]) * 100) for r in rows
                  if r["line_item_type"] in ("usage", "support"))
    rows.append({**rows[-1],
                 "line_item_id": next_id(),
                 "line_item_type": "tax",
                 "cost_category": "Tax",
                 "service": "Tax",
                 "meter": "Tax",
                 "resource_id": "tax-monthly",
                 "subscription_id": BILLING_PROFILE,
                 "subscription_name": "enrollment-level",
                 "resource_group": "",
                 "unit_price": "0.000000",
                 "quantity": "1", "unit": "Monthly",
                 "list_cost": cents(total_c * 8 // 100),
                 "ondemand_cost_equivalent": "0.000000",
                 "pre_tax_cost": cents(total_c * 8 // 100),
                 "amortized_cost": cents(total_c * 8 // 100),
                 "effective_cost": cents(total_c * 8 // 100),
                 "net_cost": "0.000000",
                 "tax_cost": cents(total_c * 8 // 100),
                 "support_fee_cost": "0.000000",
                 "marketplace_cost": "0.000000",
                 "credit_cost": "0.000000",
                 "reservation_id": "",
                 "tag_application": "", "tag_environment": "",
                 "tag_owner": "", "tag_cost_center": ""})
    # credits: July gets a monetary-commitment credit + a refund
    if period_label == "2026-07":
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "credit",
                     "cost_category": "Credit",
                     "service": "Azure Credit",
                     "meter": "MonetaryCommitment",
                     "resource_id": "mc-q3-credit",
                     "list_cost": "-25000",
                     "ondemand_cost_equivalent": "0.000000",
                     "pre_tax_cost": "-25000",
                     "amortized_cost": "-25000",
                     "effective_cost": "-25000",
                     "net_cost": "-25000",
                     "credit_cost": "-25000",
                     "quantity": "1", "unit": "USD",
                     "unit_price": "0.000000",
                     "tax_cost": "0.000000",
                     "support_fee_cost": "0.000000",
                     "marketplace_cost": "0.000000",
                     "reservation_id": ""})
        rows.append({**rows[-1],
                     "line_item_id": next_id(),
                     "line_item_type": "refund",
                     "cost_category": "Credit",
                     "service": "Azure Refund",
                     "meter": "Refund",
                     "resource_id": "refund-marketplace",
                     "list_cost": "-8000",
                     "pre_tax_cost": "-8000",
                     "amortized_cost": "-8000",
                     "effective_cost": "-8000",
                     "net_cost": "-8000",
                     "credit_cost": "-8000",
                     "quantity": "1", "unit": "USD",
                     "unit_price": "0.000000",
                     "reservation_id": ""})
    return rows


def build_all() -> dict[str, dict[str, str]]:
    """returns billing_profile → period_label → csv text"""
    rng = random.Random(SEED)
    out: dict[str, dict[str, str]] = {BILLING_PROFILE: {}}
    for p_start, p_end, label in PERIODS:
        rows = gen_month(rng, BILLING_PROFILE, label, p_start, p_end)
        if label == "2026-07":
            rows.append(dict(rows[2]))  # duplicate source row (same line_item_id)
        if label == "2026-08":
            # late-arriving correction to June usage, billed in August
            late = {**rows[0],
                    "line_item_id": f"{BILLING_PROFILE}-2026-08-LATE-000001",
                    "billing_period_start": PERIODS[0][0].isoformat(),
                    "billing_period_end": PERIODS[0][1].isoformat(),
                    "usage_start": PERIODS[0][0].replace(day=9).isoformat(),
                    "line_item_type": "adjustment",
                    "cost_category": "Adjustment",
                    "pre_tax_cost": "3100",
                    "amortized_cost": "3100",
                    "effective_cost": "3100",
                    "net_cost": "3100",
                    "list_cost": "3100",
                    "ondemand_cost_equivalent": "0.000000",
                    "quantity": "1",
                    "is_late_adjustment": "true",
                    "resource_id": "late-june-09"}
            rows.append(late)
        # August authoritative invoice total: differs from line sum by +$180
        if label == "2026-08":
            line_total_c = sum(int(Decimal(r["pre_tax_cost"]) * 100) for r in rows)
            rows.append({**rows[-1],
                         "line_item_id": f"{BILLING_PROFILE}-2026-08-SUMMARY-000001",
                         "usage_start": p_start.isoformat(),
                         "usage_end": p_end.isoformat(),
                         "billing_period_start": p_start.isoformat(),
                         "billing_period_end": p_end.isoformat(),
                         "is_late_adjustment": "false",
                         "line_item_type": "provider_summary",
                         "cost_category": "Summary",
                         "service": "Azure Invoice Summary",
                         "meter": "InvoiceTotal",
                         "resource_id": "invoice-summary",
                         "subscription_id": BILLING_PROFILE,
                         "subscription_name": "enrollment-level",
                         "resource_group": "",
                         "quantity": "0",
                         "unit": "Monthly",
                         "list_cost": "0.000000",
                         "ondemand_cost_equivalent": "0.000000",
                         "pre_tax_cost": cents(line_total_c + 18000),
                         "amortized_cost": "0.000000",
                         "effective_cost": "0.000000",
                         "net_cost": "0.000000",
                         "credit_cost": "0.000000",
                         "tax_cost": "0.000000",
                         "support_fee_cost": "0.000000",
                         "marketplace_cost": "0.000000",
                         "reservation_id": "",
                         "tag_application": "", "tag_environment": "",
                         "tag_owner": "", "tag_cost_center": ""})
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
        out[BILLING_PROFILE][label] = buf.getvalue()
    return out


def write_fixtures(root: Path) -> list[Path]:
    written: list[Path] = []
    data = build_all()
    for profile, months in data.items():
        d = root / "azure" / profile
        d.mkdir(parents=True, exist_ok=True)
        for label, text in months.items():
            path = d / f"{label}.csv"
            path.write_text(text, encoding="utf-8")
            written.append(path)
    return written


if __name__ == "__main__":
    import sys
    repo_root = Path(__file__).resolve().parents[3].parent
    files = write_fixtures(repo_root / "fixtures")
    for f in files:
        print("wrote", f, len(f.read_text(encoding="utf-8")), "bytes")
    sys.exit(0)
