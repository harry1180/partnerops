# Architecture Decision Records

| ID | Title | Status |
|----|-------|--------|
| 0001 | [Clean-room product; fixed stack](0001-stack-and-cleanroom.md) | Accepted |
| 0002 | [Monorepo layout and package boundaries](0002-monorepo-layout.md) | Accepted |
| 0003 | [Money is Decimal/NUMERIC — floats are a bug](0003-decimal-money.md) | Accepted |
| 0004 | [UUID PKs, materialized paths, soft delete](0004-keys-and-paths.md) | Accepted |
| 0005 | [RBAC with org-scoped assignments; ABAC seam](0005-rbac-abac.md) | Accepted |
| 0006 | [Path-scoped PostgreSQL RLS for tenant isolation](0006-rls-tenant-isolation.md) | Accepted |
| 0007 | [Branding is data, not code](0007-branding-data.md) | Accepted |
| 0008 | [Idempotent, restartable ingestion with quarantine](0008-ingestion-idempotency.md) | Accepted (Phase 1 hardens) |
| 0009 | [Scale by partition/rollup/jobs — not distributed infra](0009-scale-path.md) | Accepted |
| 0010 | [Cookie sessions + CSRF first; Bearer tokens for machines](0010-auth-sessions.md) | Accepted |
| 0011 | [Ingestion auto-discovers unknown accounts; mapping re-attributes cost rows](0011-account-discovery-and-reattribution.md) | Accepted |
| 0012 | [Phase-gated navigation: no fake buttons, ever](0012-phase-gated-navigation.md) | Accepted |
| 0013 | [Tailwind v4 needs a PostCSS pipeline under Next.js](0013-tailwind-postcss-nextjs.md) | Accepted |
| 0014 | [Workers use bypass scope for cross-tenant due passes, then bind per-tenant scope](0014-worker-bypass-scope.md) | Accepted |
| 0015 | [Validate cross-entity references in the service layer, not just the UI](0015-service-layer-reference-validation.md) | Accepted |
