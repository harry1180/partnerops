"""Synthetic 'our-CUR-style' adapter: CSV → CanonicalDraft stream.

The format is defined by fixtures/aws/<payer>/<period>.csv (see
synthetic_aws.py — a clean-room interchange shape, not a provider schema copy).
Validation rules:
- currency must be a known ISO code
- quantity/amounts must parse as Decimal
- usage window inside billing period unless is_late_adjustment=true
- missing linked_account → quarantine (missing_account)
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.ingestion.base import CanonicalDraft, IngestContext, ParseResult, ValidationIssue, dedupe_key

VALID_CURRENCIES = {"USD", "EUR", "GBP", "INR", "CAD", "AUD", "JPY"}


class SyntheticAwsAdapter:
    provider_code = "aws"
    parser_version = 1

    def parse(self, text: str, ctx: IngestContext) -> ParseResult:
        reader = csv.DictReader(io.StringIO(text))
        drafts: list[CanonicalDraft] = []
        issues: list[ValidationIssue] = []
        currencies: set[str] = set()
        rows_seen = 0

        def dec(v: str | None, default: Decimal = Decimal("0")) -> Decimal:
            if v in (None, ""):
                return default
            try:
                return Decimal(v)
            except InvalidOperation:
                return default

        def ts(v: str | None) -> datetime | None:
            if not v:
                return None
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None

        for i, row in enumerate(reader):
            rows_seen = i + 1
            if not row.get("line_item_id"):
                issues.append(ValidationIssue("missing_source_id", f"row {i}: line_item_id empty", i + 2))
                continue
            cur = (row.get("currency") or "").strip()
            currencies.add(cur)
            if cur not in VALID_CURRENCIES:
                issues.append(ValidationIssue("invalid_currency", f"row {i}: currency={cur!r}", i + 2))
                continue
            linked = (row.get("linked_account_id") or "").strip()
            if not linked:
                issues.append(ValidationIssue("missing_account", f"row {i}: no linked account", i + 2))
                continue
            u_start = ts(row.get("usage_start"))
            u_end = ts(row.get("usage_end"))
            if u_start is None or u_end is None:
                issues.append(ValidationIssue("bad_date", f"row {i}: usage window", i + 2))
                continue
            late = (row.get("is_late_adjustment") or "false").lower() == "true"
            if not late and (u_start < ctx.billing_period_start or u_end > ctx.billing_period_end):
                issues.append(ValidationIssue("usage_outside_period", f"row {i}", i + 2))
                continue
            quantity = dec(row.get("quantity"))
            if quantity < 0 and row.get("line_item_type") == "usage":
                issues.append(ValidationIssue("negative_quantity", f"row {i}", i + 2))
                continue
            tags = {
                "application": row.get("tag_application") or "",
                "environment": row.get("tag_environment") or "",
                "owner": row.get("tag_owner") or "",
                "cost_center": row.get("tag_cost_center") or "",
            }
            dk = dedupe_key(
                row.get("invoice_id"), row["line_item_id"], row.get("usage_start"),
                row.get("usage_end"), linked, row.get("sku"), row.get("usage_type"),
                row.get("operation"), row.get("line_item_type"),
            )
            drafts.append(CanonicalDraft(
                source_record_id=row["line_item_id"],
                dedupe_key=dk,
                row_number=i + 2,
                usage_start=u_start,
                usage_end=u_end,
                invoice_id=row.get("invoice_id"),
                payer_account=row.get("payer_account_id"),
                linked_account=linked,
                resource_id=row.get("resource_id"),
                service=row.get("service") or "Unknown",
                sku=row.get("sku"),
                usage_type=row.get("usage_type"),
                operation=row.get("operation"),
                region=row.get("region"),
                availability_zone=row.get("availability_zone"),
                quantity=quantity,
                unit=row.get("unit"),
                currency=cur,
                line_item_type=row.get("line_item_type") or "usage",
                cost_category=row.get("cost_category"),
                list_cost=dec(row.get("list_cost")),
                ondemand_equivalent=dec(row.get("ondemand_cost_equivalent")),
                provider_billed=dec(row.get("unblended_cost")),
                amortized=dec(row.get("amortized_cost")),
                effective=dec(row.get("effective_cost")),
                net=dec(row.get("net_cost")),
                credit=dec(row.get("credit_amount")),
                tax=dec(row.get("tax_amount")),
                support_fee=dec(row.get("support_fee_amount")),
                marketplace_fee=dec(row.get("marketplace_fee_amount")),
                tags=tags,
                application=tags["application"] or None,
                environment=tags["environment"] or None,
                owner=tags["owner"] or None,
                cost_center=tags["cost_center"] or None,
                source_metadata={"format": "synthetic-aws-csv-v1", "late_adjustment": late},
            ))
        return ParseResult(
            drafts=drafts,
            issues=issues,
            row_count=rows_seen,
            currency_mixed=len(currencies) > 1,
        )


class SyntheticAzureAdapter:
    """Azure Cost-Management-style CSV → CanonicalDraft stream.

    Format defined by fixtures/azure/<profile>/<period>.csv (generated by
    synthetic_azure.py — our own clean-room interchange shape). Mapping:
    billing_profile → payer leg, subscription → linked account, meter → SKU,
    pre_tax_cost → provider billed, reservation_id kept in source metadata
    for commitment coverage analytics.
    """

    provider_code = "azure"
    parser_version = 1

    def parse(self, text: str, ctx: IngestContext) -> ParseResult:
        reader = csv.DictReader(io.StringIO(text))
        drafts: list[CanonicalDraft] = []
        issues: list[ValidationIssue] = []
        currencies: set[str] = set()
        rows_seen = 0

        def dec(v: str | None, default: Decimal = Decimal("0")) -> Decimal:
            if v in (None, ""):
                return default
            try:
                return Decimal(v)
            except InvalidOperation:
                return default

        def ts(v: str | None) -> datetime | None:
            if not v:
                return None
            try:
                return datetime.fromisoformat(v)
            except ValueError:
                return None

        for i, row in enumerate(reader):
            rows_seen = i + 1
            if not row.get("line_item_id"):
                issues.append(ValidationIssue("missing_source_id", f"row {i}: line_item_id empty", i + 2))
                continue
            cur = (row.get("currency") or "").strip()
            currencies.add(cur)
            if cur not in VALID_CURRENCIES:
                issues.append(ValidationIssue("invalid_currency", f"row {i}: currency={cur!r}", i + 2))
                continue
            sub = (row.get("subscription_id") or "").strip()
            if not sub:
                issues.append(ValidationIssue("missing_account", f"row {i}: no subscription id", i + 2))
                continue
            # enrollment-scope lines (tax/credits on the billing profile itself)
            # are real bill rows but belong to no subscription — linked account
            # stays None; they still carry billing_profile as the payer leg.
            profile = (row.get("billing_profile_id") or "").strip()
            enrollment_scope = sub == profile
            u_start = ts(row.get("usage_start"))
            u_end = ts(row.get("usage_end"))
            if u_start is None or u_end is None:
                issues.append(ValidationIssue("bad_date", f"row {i}: usage window", i + 2))
                continue
            late = (row.get("is_late_adjustment") or "false").lower() == "true"
            if not late and (u_start < ctx.billing_period_start or u_end > ctx.billing_period_end):
                issues.append(ValidationIssue("usage_outside_period", f"row {i}", i + 2))
                continue
            quantity = dec(row.get("quantity"))
            if quantity < 0 and row.get("line_item_type") == "usage":
                issues.append(ValidationIssue("negative_quantity", f"row {i}", i + 2))
                continue
            tags = {
                "application": row.get("tag_application") or "",
                "environment": row.get("tag_environment") or "",
                "owner": row.get("tag_owner") or "",
                "cost_center": row.get("tag_cost_center") or "",
            }
            dk = dedupe_key(
                row.get("invoice_id"), row["line_item_id"], row.get("usage_start"),
                row.get("usage_end"), sub, row.get("meter"), row.get("line_item_type"),
                row.get("reservation_id"),
            )
            lit = row.get("line_item_type") or "usage"
            provider_billed = dec(row.get("pre_tax_cost"))
            marketplace = dec(row.get("marketplace_cost")) or (
                provider_billed if row.get("cost_category") == "Marketplace" else Decimal("0"))
            drafts.append(CanonicalDraft(
                source_record_id=row["line_item_id"],
                dedupe_key=dk,
                row_number=i + 2,
                usage_start=u_start,
                usage_end=u_end,
                invoice_id=row.get("invoice_id"),
                payer_account=row.get("billing_profile_id"),
                linked_account=None if enrollment_scope else sub,
                resource_id=row.get("resource_id"),
                service=row.get("service") or "Unknown",
                sku=row.get("meter"),
                usage_type=row.get("meter"),
                operation=row.get("subscription_name"),
                region=row.get("meter_region"),
                availability_zone=None,
                quantity=quantity,
                unit=row.get("unit"),
                currency=cur,
                line_item_type=lit,
                cost_category=row.get("cost_category"),
                list_cost=dec(row.get("list_cost")),
                ondemand_equivalent=dec(row.get("ondemand_cost_equivalent")),
                provider_billed=provider_billed,
                amortized=dec(row.get("amortized_cost")),
                effective=dec(row.get("effective_cost")),
                net=dec(row.get("net_cost")),
                credit=dec(row.get("credit_cost")),
                tax=dec(row.get("tax_cost")),
                support_fee=dec(row.get("support_fee_cost")),
                marketplace_fee=marketplace,
                tags=tags,
                application=tags["application"] or None,
                environment=tags["environment"] or None,
                owner=tags["owner"] or None,
                cost_center=tags["cost_center"] or None,
                source_metadata={
                    "format": "synthetic-azure-csv-v1",
                    "late_adjustment": late,
                    "subscription_name": row.get("subscription_name"),
                    "resource_group": row.get("resource_group"),
                    "billing_account_name": row.get("billing_account_name"),
                    "reservation_id": row.get("reservation_id") or None,
                    "unit_price": row.get("unit_price"),
                },
            ))
        return ParseResult(
            drafts=drafts,
            issues=issues,
            row_count=rows_seen,
            currency_mixed=len(currencies) > 1,
        )
