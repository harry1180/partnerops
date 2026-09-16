# 0005 — RBAC with org-scoped role assignments; ABAC-ready seam

Date: 2026-09-16 · Status: Accepted

## Context
Eight charter roles, users whose authority is bounded by an organization
subtree, and a requirement to keep future attribute-based rules additive.

## Decision
`User × Role × Organization` assignments (a single identity may hold
different roles in different tenants). Roles map to a permission catalog
(`services/authz.py`, single source of truth; `role_permissions` rows seeded
from it). Route dependencies take permission keys (`require("invoice.issue")`),
never role names — so catalog changes don't touch routes. The request
principal carries `roles`, expanded `permissions`, subtree scope prefixes,
and an `attributes` dict: the ABAC seam. Sensitive gates are permissions in
their own right: `margin.view`, `internal_notes.view`, `partner_data.view`
are absent from every customer-facing role (unit-enforced), and serializers
must consult them — the UI is never the only gate.

## Consequences
- Permission checks are cheap set lookups, cacheable per session.
- ABAC predicates attach to assignments later without schema change.
- Platform-admin is explicit (`'*'`), not an implicit bypass in every check.
