-- Cloud PartnerOps: bootstrap roles & grants for RLS-aware architecture.
-- partnerops_migrate  = superuser in local dev (schema owner, runs Alembic);
--                       in production use a least-privilege owner without BYPASSRLS
--                       plus a DBA-managed migration window (docs/deployment.md).
-- partnerops          = application role: NO SUPERUSER, NO BYPASSRLS → RLS applies.

DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'partnerops') THEN
        CREATE ROLE partnerops LOGIN PASSWORD 'cpo-app';
    END IF;
END
$$;

ALTER ROLE partnerops WITH LOGIN PASSWORD 'cpo-app';
ALTER ROLE partnerops_migrate WITH LOGIN PASSWORD 'cpo-pg-1';

GRANT CONNECT ON DATABASE partnerops TO partnerops;

-- Default privileges so every future Alembic table is usable by the app role.
ALTER DEFAULT PRIVILEGES FOR ROLE partnerops_migrate IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO partnerops;
ALTER DEFAULT PRIVILEGES FOR ROLE partnerops_migrate IN SCHEMA public
    GRANT USAGE ON SEQUENCES TO partnerops;

GRANT USAGE ON SCHEMA public TO partnerops;
