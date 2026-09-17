# ADR-0015: Validate cross-entity references in the service layer, not just the UI

Date: 2026-09-17 · Status: Accepted

## Context

The Playwright journey's pricing step 500'd because a stale async fetch left
customer B's contract version selected for customer A. The UI "looked right"
and the service trusted the pair, writing a run row whose org_path (taken
from the contract) failed RLS — a confusing 500 instead of a clear error.

## Decision

Every service entry point that accepts references belonging to another entity
validates the relationship itself (here: contract version must belong to the
priced customer → clean `ValueError` → HTTP 409 with a stable code). UIs
additionally guard against out-of-order async state (cancel stale fetches),
but the UI is never the only line of defense.

## Consequences

- Race conditions in clients produce honest 4xx errors, not 500s or misbilling.
- Invariants live next to the data they protect, where tests can reach them.
