# Cloud PartnerOps

Multi-tenant PartnerOps and cloud financial management platform for managed service providers, cloud
resellers, distributors, and enterprises running internal cloud marketplaces.

Cloud PartnerOps converts raw cloud-provider billing and usage data into accurate, auditable,
contract-aware customer charges, invoices, reports, margin analytics, optimization recommendations,
and governance evidence.

> **Clean-room product.** Cloud PartnerOps is an original implementation. It does not contain source
> code, documentation, interface designs, or trademarks of CloudCheckr, Flexera, or any competitor.

The central workflow:

```
Cloud-provider billing data
  → normalization (canonical cost model)
  → customer / account-family allocation
  → contract pricing (versioned contracts + billing rules)
  → discount and credit allocation
  → invoice generation
  → three-way reconciliation
  → customer delivery (portal)
  → audit evidence (lineage + immutable audit trail)
```

All monetary values are fixed-precision `Decimal` / PostgreSQL `NUMERIC`. Floating-point arithmetic is
never used for money.

## Technology stack

| Layer        | Choice                                             |
|--------------|----------------------------------------------------|
| Frontend     | Next.js (App Router) + TypeScript                  |
| UI           | Tailwind CSS 4 + original accessible component kit (`packages/ui`) |
| Backend API  | Python FastAPI                                     |
| Database     | PostgreSQL 16 (row-level security per tenant)      |
| ORM/migrations | SQLAlchemy 2 + Alembic                           |
| Background   | Celery workers                                     |
| Queue/cache  | Redis 7                                            |
| Object store | S3-compatible abstraction (MinIO locally, S3 in prod) |
| Auth         | Local cookie auth (argon2id); OIDC/SAML-ready design; MFA-ready |
| API spec     | OpenAPI 3 (served at `/api/openapi.json`)          |
| Local env    | Docker Compose                                     |
| Testing      | Pytest, Vitest, Playwright                         |
| Observability| structlog JSON logs, correlation IDs, Prometheus metrics, health endpoints, OTel-ready |
| Charts       | ECharts (thin wrapper)                             |

Branding (name, logo, colors, terminology) is configuration-driven via `GET /api/v1/branding` and
design tokens, so the product can be rebranded without code changes.

## Monorepo layout

```
apps/web        Next.js console + customer portal + white-labeling
apps/api        FastAPI application (models, services, billing engine, ingestion adapters)
workers         Celery worker entrypoint (imports tasks from apps/api)
packages/shared TypeScript shared types + API client
packages/ui     Accessible React component kit + chart wrapper + theme tokens
infra           Docker Compose, Dockerfiles, production templates (Terraform/CFN later)
docs            Architecture, ADRs in docs/decisions, ERD, runbooks, guides
fixtures        Deterministic synthetic billing datasets (never real customer data)
tests           Cross-cutting integration tests and Playwright journeys
```

## Quick start (local, no cloud credentials required)

Prerequisites: Docker Desktop (or Docker Engine + Compose).

```bash
cp .env.example .env          # defaults work out of the box
make up   # or: docker compose --env-file .env -f infra/docker-compose.yml up --build
```

- Web console: http://localhost:3000
- API docs (OpenAPI/Swagger): http://localhost:8000/docs
- MinIO console: http://localhost:9001 (minioadmin / minioadmin)

Migrations and demo seed run automatically on first boot (the seed is idempotent — it only runs when
the database is empty). Demo credentials for every role are listed in
[`docs/demo-credentials.md`](docs/demo-credentials.md) and on the login screen (local demo only — the
seed refuses to run when `APP_ENV` is not `local`/`test`).

### Local development without full Docker

Run only the data services in Docker and the apps on the host:

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d postgres redis minio
# API
cd apps/api && uv venv --python 3.13 && uv pip install -e ".[dev]"
alembic upgrade head && python -m app.seed && uvicorn app.main:app --port 8000
# Worker (separate shell, same venv)
celery -A app.tasks.celery_app worker --loglevel=info
# Web
pnpm install && pnpm --filter @cloudpartnerops/web dev
```

## Quality gates

From the repo root (see `Makefile`):

```bash
make check-api      # ruff + mypy + pytest (needs .env data services or docker compose up -d)
make check-web      # pnpm -r typecheck + vitest + next build
make check-all      # everything above
make up / make down # compose lifecycle
```

## Documentation map

- [docs/architecture.md](docs/architecture.md) — system architecture overview
- [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) — what works, phase by phase
- [docs/erd.md](docs/erd.md) — entity-relationship diagram (Mermaid)
- [docs/decisions/](docs/decisions/) — architecture decision records (ADRs)
- [docs/setup.md](docs/setup.md) — local setup guide
- [docs/erd.md](docs/erd.md) — entity-relationship diagram (Mermaid)
- [docs/security-and-tenant-isolation.md](docs/security-and-tenant-isolation.md)
- [docs/demo-credentials.md](docs/demo-credentials.md) — local demo accounts
- docs/billing-engine.md, invoice-lifecycle.md, reconciliation.md — Phase 1 deliverables
- [docs/cloud-connectors.md](docs/cloud-connectors.md) — ingestion contract, AWS/Azure formats, connectors (Phase 3)
- [docs/security-and-tenant-isolation.md](docs/security-and-tenant-isolation.md) — security model
- [docs/runbook.md](docs/runbook.md) — operations runbook *(grows per phase)*
- [docs/demo-script.md](docs/demo-script.md) — demonstration walkthrough *(Phase 1)*

## Implementation phases

| Phase | Scope | Status |
|-------|-------|--------|
| 0 | Foundation: monorepo, compose, DB+RLS, auth/RBAC, tenant hierarchy, navigation, design system, logging, health, seed, CI | **Complete** |
| 1 | Working AWS billing MVP: CUR import → canonical model → contracts → rules → pricing → invoices (PDF/CSV) → reconciliation → margins → audit → customer portal → e2e tests | **Complete** (see docs/IMPLEMENTATION_STATUS.md) |
| 2 | Advanced billing ops: tiers, commitments/credits allocation, maker-checker, notes, disputes, period close, leakage, scheduled reports, white-label | **Complete** (see docs/reports/phase-2-report.md) |
| 3 | Azure & multi-cloud adapter | **Complete** (see docs/reports/phase-3-report.md) |
| 4 | FinOps: budgets, forecasts, anomalies, optimization, governance | **Complete** (see docs/reports/phase-4-report.md) |
| 5 | AI PartnerOps assistant + integrations/webhooks/ERP exports | **Complete** (see docs/reports/phase-5-report.md) |
| 6 | Production hardening | Planned |

## Safety rules this project follows

- No live AWS/Azure credentials are required for local development; deterministic synthetic
  connectors and fixtures are the default. Real cloud integrations are optional.
- Nothing is deployed and no billable cloud resources are created without explicit instruction.
- No secrets in source control. Issued invoices are immutable; contract edits create new versions.
- Future features are labeled **Coming later** — no fake buttons or dead navigation.
