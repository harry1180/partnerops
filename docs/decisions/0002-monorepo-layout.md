# 0002 — Monorepo layout and package boundaries

Date: 2026-09-16 · Status: Accepted

## Context
Frontend, backend, workers, and shared contracts must evolve together with
one CI and one local command.

## Decision
Layout: `apps/web`, `apps/api` (owns models/services/engine/tasks/Alembic),
`workers` (Celery entrypoint importing from api), `packages/shared` (TS
types + display helpers), `packages/ui` (components + tokens), `infra`,
`docs`, `fixtures`, `tests` (e2e). Dependency direction is one-way:
web → (ui, shared) → api → DB. The web app consumes the API only through
`/api-backend` (Next same-origin rewrite) and OpenAPI-typed helpers.

## Consequences
- Type contracts live in two places (Python is the source of truth;
  packages/shared mirrors it) — Phase 5 adds generated client from
  openapi.json to enforce sync.
- One CI pipeline, one compose file, no cross-repo versioning pain.
