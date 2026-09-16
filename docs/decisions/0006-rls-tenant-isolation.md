# 0006 — Path-scoped PostgreSQL RLS for tenant isolation

Date: 2026-09-16 · Status: Accepted

## Context
A forgotten WHERE must never cross tenants. The charter demands database-
level isolation in addition to app checks.

## Decision
- Two database roles: owner (`partnerops_migrate`, runs Alembic, bypasses RLS
  by ownership) and application (`partnerops`, NOBYPASSRLS). Local dev and
  prod both connect the API as the app role.
- All tenant tables (43 in Phase 0) enable RLS with one policy shape:
  `left(org_path, length(current_setting('app.current_org_path'))) =
  current_setting('app.current_org_path')` for both USING and WITH CHECK —
  the GUC is set per transaction by the request dependency to the caller's
  subtree root. Unset/empty means the `@@deny@@` sentinel ⇒ zero rows.
- `organizations` uses the same predicate on `path`. `platform_admin` gets
  `/` (root), i.e. the whole tree — still policy-evaluated, not a superuser.
- Two `%`-free spellings matter operationally: psycopg3 doubles `%` in raw
  SQL (LIKE patterns were rewritten to `left()/length()`), and the same
  applies to RAISE strings.
- Append-only audit: app role has UPDATE/DELETE revoked plus a trigger that
  raises unless a session-scoped `app.audit_maintenance` gate (DBA use:
  retention purges) is on. Issued invoices: update triggers guard
  financial fields + status transitions.
- The initial migration is the enforcement source; a hand-maintained list
  of scoped tables must stay in sync with new tables (CI check Phase 1).

## Consequences
- Defense in depth: app-layer 404-on-cross-scope + DB that cannot return
  foreign rows even if the app layer is buggy.
- Real-Postgres tests (`tests/test_pg_isolation.py`) verify invisible
  cross-tenant reads, WITH-CHECK write rejection, deny-all when unscoped,
  child-subtree visibility, audit append-only (even against the table
  owner), invoice immutability.
- Operational cost: connection strings are per-role; migrations run as
  owner; local `.env` must match the compose role bootstrap.
