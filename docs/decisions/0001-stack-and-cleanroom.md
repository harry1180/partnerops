# 0001 — Clean-room product; fixed stack

Date: 2026-09-16 · Status: Accepted

## Context
The charter demands an original PartnerOps/cloud-financial platform (no copied
design/code from CloudCheckr, Flexera, etc.) with a specified stack and local
runnability without cloud credentials.

## Decision
Greenfield monorepo exactly as chartered: Next.js + TypeScript, Tailwind 4 +
an original component kit (`packages/ui`), FastAPI, PostgreSQL 16 (RLS),
SQLAlchemy 2 + Alembic, Celery + Redis, S3-compatible object storage (MinIO
locally), Pytest/Vitest/Playwright, structlog + Prometheus. All UI components
are hand-built (no third-party design system) and all wording/flows are
original. Synthetic deterministic fixtures and simulated connectors are the
default; real AWS/Azure ingestion is opt-in.

## Consequences
- Full control over accessibility and branding; more components to own.
- Familiar, boring, maintainable stack; one `docker compose` up for demo.
- Clean-room posture is a compliance feature, restated in README.
