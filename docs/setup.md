# Cloud PartnerOps — Local Setup Guide

## Prerequisites
- Docker Desktop (or Docker Engine) with Compose v2
- For host development: Node ≥ 22, pnpm ≥ 12, Python ≥ 3.12 (or `uv`)

## Option A — full stack in Docker (recommended for demo)

```bash
cp .env.example .env          # defaults work as-is
make up                       # docker compose --env-file .env -f infra/docker-compose.yml up --build
```

| Surface | URL | Credentials |
|---------|-----|-------------|
| Web console | http://localhost:3000 | see `docs/demo-credentials.md` |
| API + OpenAPI docs | http://localhost:8000/docs | — |
| MinIO console | http://localhost:9001 | `minioadmin` / see `.env` |

Migrations and the demo seed run automatically on first boot of the `api`
service; the seed is idempotent and refuses to run outside `local|test`.

```bash
make down                     # stop (volumes persist)
# fresh demo: docker volume rm infra_pgdata infra_miniodata
```

## Option B — host dev with Docker data services

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d postgres redis minio
# keep those running; api/worker/web run on the host for hot reload
```

**API** (from `apps/api`):
```bash
uv venv --python 3.13 && uv pip install -e ".[dev]"
.venv/Scripts/python.exe -m alembic upgrade head      # shell .env auto-loads via app config
.venv/Scripts/python.exe -m app.seed                  # idempotent demo seed
.venv/Scripts/python.exe run_server.py --port 8001    # use run_server.py on Windows
```
> Windows note: `run_server.py` forces a Selector event loop — psycopg3
> async cannot run on the default Proactor loop. In Linux/Docker the plain
> `uvicorn app.main:app --port 8000` is used instead (compose runs this).

**Worker** (from `apps/api`):
```bash
.venv/Scripts/python.exe -m celery -A app.tasks worker -Q default,pricing,ingestion --loglevel=info
```

**Web** (from repo root):
```bash
pnpm install
BACKEND_URL=http://localhost:8001 pnpm --filter @cloudpartnerops/web dev   # http://localhost:3000
```
The browser only ever talks to `localhost:3000`; Next.js rewrites
`/api-backend/* → BACKEND_URL/*`, keeping cookies + CSRF same-origin.

## Ports (`.env` / compose defaults)
5432 postgres · 6379 redis · 9000/9001 MinIO · 8000/8001 API · 3000 web.
If a port is taken, change the host side in `infra/docker-compose.yml` and
`.env` (`DATABASE_URL`, `REDIS_URL`, `OBJECT_STORAGE_ENDPOINT`, `BACKEND_URL`).

## Seeding & demo data
- Seed is run by the api container on boot and manually by `make seed` /
  `python -m app.seed`. Re-running is a no-op (checks for existing orgs).
- Demo datasets (synthetic AWS CUR files for Phase 1) live in `fixtures/`
  and are deterministic — same inputs, same cents, every run.

## Quality gates (run these before calling anything done)
```bash
make check-api    # ruff, mypy, pytest (set CPO_TEST_PG_URL + CPO_TEST_PG_OWNER_URL to include the RLS suite)
make check-web    # pnpm typecheck + vitest + next build
make check-all
```
RLS suite (requires the docker postgres running):
```bash
cd apps/api
CPO_TEST_PG_URL="postgresql+psycopg://partnerops:cpo-app@127.0.0.1:5432/partnerops" \
CPO_TEST_PG_OWNER_URL="postgresql+psycopg://partnerops_migrate:cpo-pg-1@127.0.0.1:5432/partnerops" \
.venv/Scripts/python.exe -m pytest tests/ -q
```

## Troubleshooting
- **`password authentication failed`** — compose interpolates from
  `--env-file .env`; make sure you use `make up` (or the `--env-file` flag),
  not a bare `-f infra/docker-compose.yml`, so defaults and roles agree.
- **`503 degraded` on /health/ready** — check each dependency via the
  response's `checks` map; recreate the named container.
- **Proactor event loop error (Windows)** — you ran bare `uvicorn`; use
  `run_server.py`.
- **`.next/standalone` symlink EPERM on Windows builds** — expected without
  dev-mode; `output: "standalone"` is enabled only in the Docker image.
- **pnpm "Ignored build scripts"** — see `pnpm-workspace.yaml`
  `onlyBuiltDependencies`/`allowBuilds` (esbuild, sharp).
