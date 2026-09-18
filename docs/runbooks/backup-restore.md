# Backup & restore runbook (PostgreSQL + MinIO)

Commands below were **executed as a drill on 2026-09-18** against the local
docker stack and produced a bit-for-bit-restorable database: pg_dump -Fc →
create → pg_restore → row counts and RLS flags identical to source
(59 tables, 17 invoices, 575 audit events, 50 RLS-enabled — both sides).
Substitute real hostnames/credentials from deployment config; never store
this file's placeholders in git.

## PostgreSQL — backup (custom format, compresses, selective restore)

    docker exec cloud-partnerops-postgres-1 \
      sh -c 'PGPASSWORD=*** pg_dump -U partnerops_migrate -d partnerops \
             -Fc -f /tmp/cpo_$(date -u +%Y%m%dT%H%M%SZ).dump'

    # ship it off-box (host mount, object storage, or scp) — a backup next
    # to the database is not a backup:
    docker cp cloud-partnerops-postgres-1:/tmp/cpo_<stamp>.dump ./backups/

Cadence (production): `pg_dump -Fc` nightly + WAL archiving (PITR) via the
managed service when available. `audit_events` is append-only — logical
backups capture it fully; verify counts after restore, never before only.

## PostgreSQL — restore drill (into a scratch database)

    docker exec cloud-partnerops-postgres-1 sh -c 'psql -U partnerops_migrate \
      -d postgres -c "CREATE DATABASE cpo_restore_drill"'
    docker exec cloud-partnerops-postgres-1 sh -c \
      'PGPASSWORD=*** pg_restore -U partnerops_migrate -d cpo_restore_drill \
       /tmp/cpo_<stamp>.dump'
    # verify parity vs source:
    docker exec cloud-partnerops-postgres-1 psql -U partnerops_migrate \
      -d cpo_restore_drill -t -A -c "SELECT
        (SELECT count(*) FROM pg_tables WHERE schemaname='public') AS tables,
        (SELECT count(*) FROM invoices) AS invoices,
        (SELECT count(*) FROM audit_events) AS audit,
        (SELECT count(*) FROM pg_tables WHERE schemaname='public'
           AND rowsecurity) AS rls_on"
    # clean up the drill:
    docker exec cloud-partnerops-postgres-1 psql -U partnerops_migrate \
      -d postgres -c 'DROP DATABASE cpo_restore_drill'

Restore-to-production of a scratch dump means: stop API+workers, restore
into a NEW database name, flip `DATABASE_URL`, smoke `/health/ready` +
`smoke_phase1_live`, then re-point. Never restore over a live schema.

RLS note: policies are stored in the dump and come back with the schema
(the parity check counts `rowsecurity=t` tables on both sides). The app
role (non-owner) must remain NOBYPASSRLS — `init.sql` provisions it; re-
check after any restore into a fresh cluster:

    SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = 'partnerops';

## Object storage (MinIO) — raw billing files

    docker run --rm --network cloud-partnerops_cpo \
      quay.io/minio/mc alias set drill http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
    docker run --rm --network cloud-partnerops_cpo \
      quay.io/minio/mc mirror --overwrite drill/partnerops-raw ./backups/raw-$(date -u +%Y%m%d)/

Exports/invoices regenerate deterministically from canonical rows (Phase 2
design); the *irreplaceable* objects are raw provider files — back those
up with the database, and keep the same retention window for both.

## Secrets

- `SECRET_ENCRYPTION_KEY` custody lives OUTSIDE the repo (secret manager /
  KMS-issued key via the deployment environment). Losing it = losing every
  sealed webhook signing secret (endpoints get rotated, not recovered).
- Back it up wherever your platform stores deployment secrets; test that
  path on the same cadence as the DB drill.

## Drill schedule

Quarterly: run restore drill end-to-end, record date + row-count parity in
a note under this file (append a `## Drill log` section). An untested
backup is a rumor.
