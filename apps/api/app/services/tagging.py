"""Cloud-provider tagging conventions (Phase 4).

Allocation tags are the join between raw provider metadata and our canonical
dimensions. Ingestion maps provider tag columns onto the canonical dimension
columns (application / environment / owner / cost_center); this module defines
the contract the governance engine and tag-compliance reporting evaluate
against.

Environment values normalize to prod|nonprod; anything else counts as
unknown and contributes to unallocated-analysis.

Unallocated cost = canonical usage rows with no customer attribution — the #1
FinOps governance failure; tag coverage and the unallocated_cost policy both
target it.
"""

from __future__ import annotations

from dataclasses import dataclass

REQUIRED_TAG_KEYS = ("application", "environment", "owner", "cost_center")

PROD_VALUES = {"prod", "production", "live"}
NONPROD_VALUES = {"dev", "test", "stage", "staging", "nonprod", "sandbox", "qa"}


def normalize_environment(value: str | None) -> str:
    if not value:
        return "unknown"
    v = value.strip().lower()
    if v in PROD_VALUES:
        return "prod"
    if v in NONPROD_VALUES:
        return "nonprod"
    return "unknown"


@dataclass(frozen=True)
class TagCoverage:
    compliant: int
    total: int

    @property
    def pct(self) -> float:
        if self.total == 0:
            return 100.0
        return round(100.0 * self.compliant / self.total, 2)
