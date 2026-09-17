"""Validate the generated fixtures satisfy every charter scenario."""
import csv
import hashlib
import io
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ingestion.synthetic_aws import build_all  # noqa: E402

months = build_all()["777700000001"]
assert hashlib.sha256(months["2026-06"].encode()).hexdigest() == \
    hashlib.sha256(build_all()["777700000001"]["2026-06"].encode()).hexdigest(), "not deterministic"

for label, text in months.items():
    rows = list(csv.DictReader(io.StringIO(text)))
    ids = [r["line_item_id"] for r in rows]
    dupe = len(ids) - len(set(ids))
    total = sum(Decimal(r["unblended_cost"]) for r in rows if r["line_item_type"] == "usage")
    print(label, "rows", len(rows), "exact-dup-ids", dupe,
          "usage-total", f"{total:.2f}",
          "late", sum(1 for r in rows if r["is_late_adjustment"] == "true"))

rows = list(csv.DictReader(io.StringIO(months["2026-07"])))
ids = [r["line_item_id"] for r in rows]
assert len(ids) - len(set(ids)) == 1, "expected one duplicated line_item_id"

cob = [r for r in rows if r["linked_account_id"] == "444444444444" and r["line_item_type"] == "usage"]
cob_sum = sum(Decimal(r["unblended_cost"]) for r in cob)
acme = [r for r in rows if r["linked_account_id"] == "111111111111" and r["line_item_type"] == "usage"]
print("cobalt usage", f"{cob_sum:.2f}", "acme usage", f"{sum(Decimal(r['unblended_cost']) for r in acme):.2f}")

aug = list(csv.DictReader(io.StringIO(months["2026-08"])))
cf = [Decimal(r["unblended_cost"]) for r in aug
      if r["linked_account_id"] == "444444444444" and "Tier1" in r["usage_type"]]
jun = [Decimal(r["unblended_cost"]) for r in rows
       if r["linked_account_id"] == "444444444444" and "Tier1" in r["usage_type"]]
print("anomaly: cobalt cloudfront Jun", jun, "Aug", cf)
assert cf and jun and cf[0] / jun[0] >= 10, "anomaly not visible"

un = [r for r in aug if r["linked_account_id"] == "999999999999"]
assert un, "missing unassigned account"
jrows = list(csv.DictReader(io.StringIO(months["2026-07"])))
assert any(Decimal(r["credit_amount"]) < 0 for r in jrows), "no credit rows"
assert any(r["line_item_type"] == "refund" for r in jrows), "no refund rows"
print("FIXTURES_OK")
