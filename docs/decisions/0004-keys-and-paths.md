# 0004 — UUID PKs, materialized org paths, soft delete

Date: 2026-09-16 · Status: Accepted

## Context
Multi-tenant URLs and IDs must not leak cardinality or let users enumerate
neighbors; hierarchy queries must be index-friendly; business records are
never destroyed.

## Decision
- UUIDv4 primary keys everywhere.
- `organizations.path` is a materialized path of org UUIDs with leading and
  trailing `/` (e.g. `/plat/dist/msp/cust/`). Children append their id;
  subtree checks are prefix comparisons (`left(path, length(scope)) = scope`)
  which keep `path` index-usable.
- Every tenant-scoped row duplicates the path as `org_path` (not derived by
  join) so RLS and scope filters are single-column and index-friendly.
- Business records carry `deleted_at` (soft delete); reads filter, migrations
  preserve history; hard deletes never happen from the application.
- `mapped` columns without explicit SQLAlchemy types are rejected — the
  annotation rule that saved us during autogenerate (date/time columns must
  carry `DateTime(timezone=True)` explicitly).

## Consequences
- UUIDs bloat indexes slightly vs bigserial; acceptable for 25k accounts.
- Re-parenting an org rewrites descendant paths — rare admin operation;
  Phase 6 adds a guarded re-parent script.
- `org_path` denormalization needs care on move; service layer owns it.
