"""Provider-neutral ingestion framework.

An adapter turns one source file into a stream of `CanonicalDraft`s — the
provider-agnostic shape that the ingest service persists as
RawBillingRecord + CanonicalCostRecord. Adapters are pure functions over
(rows, context): no DB access, so they can be tested and replayed offline.

Invariants enforced by the service, not the adapters:
- idempotency: files unique on (sha256, parser_version); re-ingest is a no-op
- dedupe: `dedupe_key` = sha256 over semantic identity; collisions are counted
  and skipped (never double-charged)
- quarantine: rows that fail validation land in quarantined_records with a
  reason — never silently dropped
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class IngestContext:
    """Everything needed to interpret a file outside provider payloads."""

    org_id: str
    org_path: str
    provider_code: str
    parser_version: int
    billing_period_start: datetime
    billing_period_end: datetime
    correlation_id: str | None = None


@dataclass
class ValidationIssue:
    reason: str          # invalid_currency | bad_quantity | missing_account | bad_date | ...
    message: str
    row_number: int


@dataclass
class CanonicalDraft:
    """Provider-neutral cost record, amounts already Decimal."""

    source_record_id: str
    dedupe_key: str
    row_number: int
    usage_start: datetime
    usage_end: datetime
    invoice_id: str | None
    payer_account: str | None
    linked_account: str | None
    resource_id: str | None
    service: str
    sku: str | None
    usage_type: str | None
    operation: str | None
    region: str | None
    availability_zone: str | None
    quantity: Decimal
    unit: str | None
    currency: str
    line_item_type: str
    cost_category: str | None
    list_cost: Decimal
    ondemand_equivalent: Decimal
    provider_billed: Decimal
    amortized: Decimal
    effective: Decimal
    net: Decimal
    credit: Decimal
    tax: Decimal
    support_fee: Decimal
    marketplace_fee: Decimal
    tags: dict[str, str] = field(default_factory=dict)
    application: str | None = None
    environment: str | None = None
    owner: str | None = None
    cost_center: str | None = None
    source_metadata: dict = field(default_factory=dict)


@dataclass
class ParseResult:
    drafts: list[CanonicalDraft]
    issues: list[ValidationIssue]
    row_count: int
    currency_mixed: bool = False


class BillingFileAdapter(Protocol):
    """One adapter per provider export format."""

    provider_code: str
    parser_version: int

    def parse(self, text: str, ctx: IngestContext) -> ParseResult: ...


def dedupe_key(*parts: str | None) -> str:
    joined = "|".join(p if p is not None else "" for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()
