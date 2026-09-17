"""Adapter unit tests against the deterministic fixtures (no DB)."""

from decimal import Decimal
from pathlib import Path

import pytest

from app.ingestion.adapters import SyntheticAwsAdapter
from app.ingestion.base import IngestContext
from app.ingestion.synthetic_aws import PERIODS, build_all

REPO = Path(__file__).resolve().parents[4]


def ctx(label: str) -> IngestContext:
    start, end, lbl = next(p for p in PERIODS if p[2] == label)
    return IngestContext(
        org_id="00000000-0000-4000-8000-000000000000",
        org_path="/",
        provider_code="aws",
        parser_version=1,
        billing_period_start=start,
        billing_period_end=end,
    )


@pytest.fixture(scope="module")
def months():
    return build_all()["777700000001"]


def test_adapter_parses_all_months(months):
    a = SyntheticAwsAdapter()
    for label, text in months.items():
        res = a.parse(text, ctx(label))
        assert res.drafts, label
        assert res.row_count >= len(res.drafts)


def test_canonical_amounts_are_decimal(months):
    res = SyntheticAwsAdapter().parse(months["2026-06"], ctx("2026-06"))
    d = res.drafts[0]
    assert isinstance(d.provider_billed, Decimal)
    assert isinstance(d.list_cost, Decimal)


def test_july_has_service_credit_and_refund(months):
    res = SyntheticAwsAdapter().parse(months["2026-07"], ctx("2026-07"))
    types = {d.line_item_type for d in res.drafts}
    assert "credit" in types and "refund" in types


def test_duplicate_dedupe_keys_detected(months):
    res = SyntheticAwsAdapter().parse(months["2026-07"], ctx("2026-07"))
    keys = [d.dedupe_key for d in res.drafts]
    assert len(keys) - len(set(keys)) == 1  # the exact duplicated source row


def test_late_adjustment_in_august(months):
    res = SyntheticAwsAdapter().parse(months["2026-08"], ctx("2026-08"))
    late = [d for d in res.drafts if d.usage_start < ctx("2026-08").billing_period_start]
    assert len(late) == 1
    assert late[0].line_item_type == "adjustment"
    # validation kept it because is_late_adjustment=true
    assert not any(i.reason == "usage_outside_period" for i in res.issues)


def test_tags_map_to_dimensions(months):
    res = SyntheticAwsAdapter().parse(months["2026-06"], ctx("2026-06"))
    tagged = [d for d in res.drafts if d.application]
    assert tagged
    assert all(isinstance(d.tags, dict) for d in tagged)


def test_quarantine_on_bad_currency(tmp_path):
    a = SyntheticAwsAdapter()
    text = (
        "billing_period_start,billing_period_end,usage_start,usage_end,line_item_id,"
        "invoice_id,line_item_type,cost_category,payer_account_id,linked_account_id,"
        "resource_id,service,sku,usage_type,operation,region,availability_zone,quantity,"
        "unit,currency,public_ondemand_unit_price,list_cost,ondemand_cost_equivalent,"
        "unblended_cost,amortized_cost,effective_cost,net_cost,credit_amount,tax_amount,"
        "support_fee_amount,marketplace_fee_amount,tag_application,tag_environment,"
        "tag_owner,tag_cost_center,is_late_adjustment\n"
        + ",".join([
            "2026-06-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00",
            "2026-06-01T00:00:00+00:00", "2026-06-01T01:00:00+00:00",
            "bad-1", "INV", "usage", "Compute", "777700000001", "111111111111",
            "r", "s", "sku", "ut", "op", "us-east-1", "az", "1", "Hrs", "XYZ",
            "1", "1", "1", "1", "1", "1", "1", "0", "0", "0", "0", "", "", "", "", "false",
        ]) + "\n"
    )
    res = a.parse(text, ctx("2026-06"))
    assert not res.drafts
    assert res.issues[0].reason == "invalid_currency"
