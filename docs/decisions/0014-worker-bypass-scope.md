# ADR-0014: Workers use bypass scope for cross-tenant due passes, then bind per-tenant scope

Date: 2026-09-17 · Status: Accepted

## Context

`run_due_schedules` must find due report schedules across ALL tenants. RLS
deny-all (unset `app.current_org_path`) makes that impossible by design, and
the first implementation silently returned zero rows on live Postgres while
sqlite tests (no RLS) passed.

## Decision

A single documented, audited bypass scope (`set_bypass_scope`, the same
mechanism the seed script uses) is bound ONLY for the due-schedule discovery
query. Each schedule is then processed under ITS OWN org scope: generation,
storage, outbox and audit all run as the schedule's tenant via a synthetic
system principal holding only `report.read`/`cost.read`. The bypass principal
is never constructible from web routes.

## Consequences

- Cross-tenant worker passes are explicit, narrow, and testable; no RLS hole.
- Live-Postgres coverage is mandatory for anything touching scope binding —
  sqlite-only tests cannot catch RLS semantics (proven by this bug).
