# Security & Tenant-Isolation Model

Threat-oriented summary; ADRs in `docs/decisions/` carry the rationale.

## Tenancy

- Hierarchy: platform → distributor → reseller/MSP → customer (materialized
  `path`; every tenant row stores `org_path`).
- **Two DB roles**: schema owner (migrations only) and `partnerops` app role
  (NOBYPASSRLS). RLS is always on for the app role — including when the
  session forgot to set scope, which yields deny-all (`@@deny@@` sentinel).
- **Defense in depth:** (1) request dependency binds
  `app.current_org_path` to the caller's subtree per transaction; (2) every
  query filters by permission + scope; (3) RLS makes a forgotten filter
  harmless; (4) cross-tenant probes answer 404, never 403-with-content or
  existence hints.
- Customer portal reads are additionally *serialized* through
  permission-checked views: `margin.view` / `internal_notes.view` /
  `partner_data.view` are absent from all customer roles, enforced in code
  and by the authz unit tests, not just hidden in the UI.
- Verified by `tests/test_pg_isolation.py` against real PostgreSQL:
  cross-tenant invisible, WITH CHECK blocks forged-path writes, child
  subtree visible, unscoped deny-all, audit append-only even for the owner,
  issued-invoice immutable.

## Authentication

argon2id hashing (never any other password path); opaque session tokens
(256-bit, SHA-256 stored, HttpOnly/SameSite=Lax/Secure-in-prod cookies);
server-side revocation (logout, disable-user revokes all sessions); failed
login count → 15-min lockout; per-IP+email rate limit (Redis, memory
fallback). OIDC/SAML via `IdentityProvider` interface (same session-issuing
path); MFA fields + login hook ready (enforcement Phase 5). API tokens:
`cpo_…` bearer, hash-only, expiry/rotation, scopes intersect creator
permissions.

## Authorization

Central permission catalog (`services/authz.py`); routes declare required
permissions; role→permission graph seeded; maker-checker table exists for
high-impact actions (enforced by billing services from Phase 2). Auditor
roles are read-only by construction (unit-enforced).

## Injection / common web risks

- SQL: SQLAlchemy Core/ORM bound parameters everywhere; raw SQL only in
  migrations/ops with constants; input never concatenated.
- XSS: React escaping; no `dangerouslySetInnerHTML`; CSP headers on API
  responses; logo serving is content-sniffed + typed.
- CSRF: double-submit (readable cookie + header echo) checked
  timing-safely, fail-closed, and ordered *after* auth so it can't bypass.
- SSRF: outbound URLs (webhooks/integrations) validated scheme+host at
  config time; fetches disabled until Phase 5 approval flow ships (no fake
  "test webhook" button).
- File uploads: type allowlist (PNG/JPEG), size cap, magic-byte check,
  stored in object storage under hashed keys, served via auth-scoped route.
- Headers: nosniff, frame-ancestors DENY, referrer no-referrer,
  Cache-Control no-store on API, HSTS outside local.
- Secrets: only in `.env`/runtime env (never committed — gitignore +
  `.env.example` placeholders); secrets inside integration config are
  stored as `secret_refs`, redaction enforced on all audit detail payloads;
  demo seed refuses non-local envs.

## Data protection lifecycle

- Soft delete for business records; hard delete never from the app.
- Issued invoices/credit notes immutable (triggers) — corrections via notes.
- Audit trail append-only (revokes + trigger + gated maintenance mode).
- Encryption: TLS in transit (terminate at LB/WAF in prod; KMS + RDS/PgBouncer
  at-rest story in deployment docs); local dev is plaintext by necessity
  (documented).
- Retention: raw billing files retained for audit; purge plan (with
  documented legal hold) ships Phase 6.

## Least-privilege cloud connectors

Connector roles (Phase 1/3) are read-only S3/Cost Management scoped to the
billing bucket/EA; credentials live in Secrets Manager/KMS; no
purchase/modify cloud permissions in release 1 (charter rule honored —
recommendation UIs only).

## Known Phase-0 gaps (tracked)

MFA enrollment flow, SAML/OIDC adapters (interfaces only), webhook delivery
(none yet — no dead buttons), per-IP org enumeration lockout on
`/admin/demo-accounts` (local-only endpoint), CSP is API-side; browser CSP
ships with Phase 1 when inline styles are gone.
