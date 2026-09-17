# ADR-0012: Phase-gated navigation manifest

Date: 2026-09-16
Status: Accepted

## Context

The charter forbids fake buttons and dead navigation. The nav is driven by a
server-side manifest (`NAVIGATION` in `app/api/v1/health_misc.py`) with a
`permission` and an `available_from_phase` per entry. Two failure modes
appeared in review:

1. An entry could be *granted* (permission held) while its page did not exist
   yet in this release → clicking 404s.
2. A page could exist but be missing from the nav's group list → unreachable
   (the Pricing Runs page shipped orphaned).

## Decision

- The capabilities endpoint now returns `current_phase` (single constant in
  the API, bumped at each phase gate).
- ConsoleShell renders a nav item as a live `<Link>` only when
  `granted && available_from_phase <= current_phase`; otherwise it renders a
  disabled row labeled **"Coming later"** (or `Phase N` when the permission is
  missing). Disabled rows are not links and carry no click handler.
- `Reports` was moved from Phase 1 to Phase 2 because its page does not exist
  yet; `Cloud Accounts` and `Pricing Runs` were verified to have real pages
  before being marked Phase 1.
- CI-adjacent convention: any PR adding a route must add the manifest entry
  with `available_from_phase <= CURRENT_PHASE` only when the page compiles.

## Consequences

- The UI can never 404 from the main nav, even when a deployment is on an
  older build than the manifest expectation.
- The manifest remains the single source of truth; the UI adds no hardcoded
  routes of its own (portal nav is a separate, smaller manifest).
