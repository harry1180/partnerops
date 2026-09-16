# 0010 — Cookie sessions + CSRF first; Bearer tokens for machines

Date: 2026-09-16 · Status: Accepted

## Context
A first-party web console plus customer portal and a future integrator
surface (ERP, BI, automation), with OIDC/SAML/MFA readiness required later.

## Decision
- Humans: server-side sessions (opaque 256-bit token, hash-only storage,
  HttpOnly + SameSite=Lax cookie; Secure forced off-local), Next.js rewrites
  keep the browser same-origin so CSRF/cookie rules stay simple. CSRF is
  double-submit: a readable cookie token must be echoed in
  `X-CSRF-Token` on unsafe methods — checked timing-safely, fail-closed,
  ordered after auth so it can never fail open. Logout revokes the session;
  disable-user revokes all sessions immediately.
- Machines: `Authorization: Bearer cpo_…` scoped API tokens (hash-only,
  expiry, rotation). Scopes INTERSECT the creator's permissions — a token
  can never be wider than a human, and cross-org token use is 401/404.
- Rate limiting on login (per IP+email, Redis-backed, memory fallback);
  8 failures → 15-minute lockout; failed logins audited with the IP.
- IdentityProvider is an interface: LocalIDP ships now; OIDC (auth-code
  callback) and SAML (ACS route) implement the same session-issuing path
  later — no route changes. MFA fields/flow hooks exist (`mfa_secret`,
  enforcement at login) pending Phase 5 rollout.
- JWTs deliberately avoided: revocation, session-bound CSRF, and
  audit-traceable sessions matter more than stateless tokens here.

## Consequences
- Sessions must be pruned (Celery beat task exists). Horizontal scaling is
  cookie-affinity-free because sessions live in Postgres.
