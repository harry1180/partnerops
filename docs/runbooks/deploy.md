# Deployment runbook (staging/production)

The compose stack in `infra/` runs the full system locally; production
means the same images with the hardening requirements below enforced.
Boot-time refusals are deliberate (`config.validate_for_boot`) — the app
will NOT start misconfigured.

## Required configuration (non-local)

| Key | Requirement |
|-----|-------------|
| `APP_ENV` | `staging` or `production` |
| `SECRET_KEY` | real random value (placeholder → boot refuses) |
| `SECRET_ENCRYPTION_KEY` | Fernet key; `python -c "from app.core.secretbox import generate_key; print(generate_key())"`. Custody: platform secret manager / KMS-issued env. Missing → boot refuses. |
| `TRUSTED_HOSTS` | exact comma-separated hostnames served (e.g. `ops.example.com`). `*` → boot refuses. |
| `CORS_ORIGINS` | exact web origin(s) (`https://app.example.com`), never `*` with credentials |
| `DATABASE_URL` | app role = non-owner, **NOBYPASSRLS** (RLS is the tenant wall; owner role is migrate-only: `DATABASE_URL_MIGRATE`) |
| TLS | terminate at the proxy; HSTS is emitted automatically outside local/test |

Cookie flags (`secure`, `httponly`, `samesite=lax`) already follow
`APP_ENV` — nothing to configure; do not front the API without TLS.

## Bring-up (docker compose shape)

    git clone <repo> && cd repo
    cp .env.example .env    # fill required values above; chmod 600 .env
    docker compose --env-file .env -f infra/docker-compose.yml up -d --build
    docker exec <api> alembic upgrade head   # or the api container's entrypoint hook
    # seed only for demo/staging; production creates orgs via the API:
    python -m app.seed
    curl -fsS http://<api>/health/ready      # db + redis verified
    # behind the proxy: TRUSTED_HOSTS must include every Host header the
    # proxy forwards (health checks from inside the network included).

Web: `NEXT_PUBLIC_API_URL` is baked at image build (`infra/web.Dockerfile`
arg); `BACKEND_URL` is runtime (server-side `/api-backend` rewrite). Both
must point at the same API.

Workers: run Celery worker + beat (`workers/` scripts or equivalents):

    celery -A app.tasks worker -Q default,pricing,ingestion -l info
    celery -A app.tasks beat   -l info

Scheduled jobs (beat): session prune nightly; scheduled reports 15m;
connectors 15m; nightly FinOps passes 03:40 UTC; integrations sweep 5m.

## Post-deploy verification

From `apps/api` against the deployed host (read-only + self-cleaning
fixtures):

    python tools/smoke_phase1_live.py https://<api>
    python tools/smoke_phase4_live.py https://<api>
    python tools/smoke_phase5_live.py https://<api>   # webhook events end-to-end

Plus manual: sign in as an operator, confirm `/metrics` is firewalled
(internal only), check `logs/` for `webhook_delivery_error` noise.

## Upgrade / rollback

    alembic upgrade head          # forward; migrations are additive-only
    alembic downgrade -1          # per-release downgrades exist in every
                                  # migration file (verified in CI-less
                                  # drills; run against a restored copy first)

Rollback = redeploy previous image + `alembic downgrade` ONLY if the new
release shipped data the old code can't tolerate; otherwise old image +
new schema is fine (columns are additive/nullable by convention).

## Egress & SSRF

Application-level checks (scheme, metadata ranges, private-IP policy,
DNS-answer validation) ship in the product; the deployment layer adds:
- outbound webhook traffic through an egress proxy or security group that
  allows 443 to public IPs only (blocks DNS-rebinding TOCTOU gap and any
  future app-level check gap),
- object storage access from the API/worker subnets only.

## Backup

See `docs/runbooks/backup-restore.md` — quarterly restore drills are
mandatory; the drill commands there are executed, not aspirational.
