# Implementation Status

Working source of truth for what is verified. Updated at every phase gate.
Last update: Phase 0 — complete.

## Phase 0 — Foundation ✅ COMPLETE

Verified by execution (not aspiration):

| Area | Status | Evidence |
|------|--------|----------|
| Monorepo scaffold (web/api/workers/packages/infra/docs) | ✅ | `pnpm install` clean, workspace scripts wired |
| Docker Compose stack | ✅ | postgres/redis/minio `Up (healthy)`; api+worker services defined (run via compose on Linux; on Windows host dev use `run_server.py`) |
| Initial migration: 54 tables | ✅ | `alembic upgrade head` applied to real PG 16.15 |
| PostgreSQL RLS on 43 tenant tables + organizations | ✅ | `tests/test_pg_isolation.py` — 6 passing against live PG (deny-all, cross-tenant invisibility, WITH-CHECK write reject, child subtree) |
| Audit trail append-only (grants + trigger + maintenance gate) | ✅ | pg test: UPDATE blocked even for table owner |
| Issued-invoice immutability (trigger) | ✅ | pg test: financial-field + illegal-transition blocks |
| AuthN: argon2id, sessions, CSRF (fail-closed), rate limit, lockout | ✅ | 30 unit/integration tests + live curl flow |
| RBAC: 8 roles / 50 permissions / scoped assignments; customer roles can't see margin | ✅ | `test_authz.py` matrix assertions |
| Org hierarchy: platform → distributor → MSP → customer | ✅ | seed + live API |
| Tenant-scoped APIs: orgs, customers, account families, users, branding, audit, admin tokens (create/rotate), capabilities | ✅ | live requests (200/201) + API tests |
| Cross-tenant probing answers 404 (no existence leak) | ✅ | tests |
| Branding: nearest-ancestor resolution + white-label override + public fallback | ✅ | tests incl. MSP "Northwind CloudBill" override |
| Seed framework (idempotent, env-gated) | ✅ | re-run skips; 11 orgs / 9 users / 8 roles |
| Celery skeleton: app, queues, beat schedule, prune task | ✅ | worker image builds; task unit smoke |
| Structured logs, correlation IDs, secure headers, /health + /metrics | ✅ | middleware; live checks |
| Quality gates | ✅ | ruff 0, mypy 0, pytest 36/36 (sqlite + real PG), typecheck clean, vitest 8/8, `next build` succeeds |
| ADRs 0001–0010 + architecture.md + erd.md | ✅ | docs/ |

### Known limitations at this gate (honest list)

1. **Windows host dev**: uvicorn must run via `apps/api/run_server.py` (psycopg3
   needs a Selector event loop; plain `uvicorn` uses Proactor on Windows).
   In Docker (Linux) plain `uvicorn` is fine.
2. **Compose on Windows**: `docker compose --env-file .env -f infra/docker-compose.yml
   up` — the Makefile `up` target already encodes this. MinIO pulls from
   quay.io (Docker Hub rate limits hit during setup).
3. Object-storage healthcheck runs a LIST (no write) — bucket creation on
   startup is exercised; deeper storage tests come with Phase 1 ingestion.
4. OIDC/SAML are interfaces, not adapters yet (charter says "ready", met).
5. MFA columns + login hooks exist; enrollment flow is Phase 5.
6. The RLS table list in the migration is maintained by hand; a CI
   consistency check (model vs policy) lands with Phase 1.

## Phase 1 — Working AWS billing MVP ⬜ NEXT

Scope: synthetic AWS CUR import → canonical model → contracts → core billing
rules → pricing runs → invoice calc (PDF+CSV) → provider↔invoice
reconciliation → margin dashboard → customer portal → end-to-end Playwright
journey. Design seams from Phase 0 feed this directly.

## Phase 2 — Advanced billing ops ⬜
Tiered pricing, commitment/credit allocation, maker-checker enforcement UI,
credit/debit notes, disputes, period closing, revenue leakage, scheduled
reports, full white-label.

## Phase 3 — Azure & multi-cloud ⬜
Cost-export adapter, subscriptions/resource groups/meters, reservations,
provider reconciliation leg, GCP adapter interface kept honest as planned-only.

## Phase 4 — FinOps & governance ⬜
Budgets, forecasts (simple, labeled), statistical anomalies, rightsizing/idle
recommendations, commitment coverage, tag compliance, policy engine +
findings/exceptions.

## Phase 5 — AI assistant & integrations ⬜
Permission-aware retrieval assistant (deterministic demo mode), evidence
citations, signed webhooks, ERP export formats, notifications, OIDC/SAML
adapters, integration admin UI.

## Phase 6 — Production hardening ⬜
Perf tests, security review, isolation audit, a11y pass, backup/restore
drill, DR + runbooks, retention config, deploy templates, release checklist.
