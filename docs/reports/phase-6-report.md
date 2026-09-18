# Phase 6 report — production hardening

Date: 2026-09-18 · final phase; charter feature scope complete

## Hardening shipped

| Area | What changed |
|------|--------------|
| Secrets at rest (ADR-0019) | `app/core/secretbox.py`: Fernet (`cryptography`) seals tenant webhook signing secrets under a dedicated `SECRET_ENCRYPTION_KEY` (separate from session-signing `SECRET_KEY`). Stored form: `enc:v1:<fernet>` — DB dumps yield ciphertext. `kms:v1:` refs raise unless a provider is configured (never fake success). Pre-hardening `local:v1:` rows still deliver until rotated. |
| Boot validation | Outside local/test the API now refuses to start with: placeholder SECRET_KEY (was already enforced), missing/invalid Fernet SECRET_ENCRYPTION_KEY, or TRUSTED_HOSTS unset/`*`. Verified matrix: all four refusals + the clean-boot path exercised directly. |
| SSRF | New `resolve_error()`: resolve the target hostname and refuse if ANY DNS answer is disallowed (kills public-name→127.0.0.1), applied at endpoint creation AND every send. IP checks extended (multicast/unspecified). Residual DNS-rebinding TOCTOU documented as deployment-egress scope (proxy/SG) — honest limit, not silent. |
| Test-send correctness | `integration.test` now actually tests the clicked endpoint (`force_endpoint_id`) instead of quietly queueing zero when the endpoint's subscription list doesn't include the test event (real bug found by the sealed-secret test). |
| Header security | API: host-header allowlist via TrustedHostMiddleware when TRUSTED_HOSTS set (security headers were already Phase 0 — nosniff/frame-ancestors/no-referrer/CSP/no-store/HSTS-in-prod; a duplicate middleware added this phase was caught and removed). Web: prod-only security headers + CSP (`script-src 'self'`, `frame-ancestors 'none'`, style `'unsafe-inline'` documented for branding attrs). Dev excluded deliberately (Next dev HMR scripts would break a strict CSP). Verified by serving `next start` on :3001 from a fresh build and checking the response headers; dev journey + prod page both 200. |
| Secret rotation | `POST /integrations/webhooks/{id}/rotate-secret` (shown once, sealed, audited) + UI button + receiver-coordination note. |
| Backup/restore runbook | `docs/runbooks/backup-restore.md` — commands executed as a real drill: pg_dump -Fc → restore into scratch DB → parity (59 tables / 17 invoices / 575 audit rows / 50 RLS-enabled identical both sides), scratch DB dropped after. |
| Migration drill | On a scratch *copy* (never the live DB): `alembic downgrade -1` (8bd7518cfa18 → c9a4d1e7f302, drops the 6 FinOps tables) → `upgrade head` → data + RLS parity intact (17 invoices, 50 RLS tables). All 4 migrations have downgrade paths; CI does not currently exercise them (honest note). |
| Deploy runbook | `docs/runbooks/deploy.md`: required non-local config table, bring-up, worker/beat commands, post-deploy smoke list, upgrade/rollback policy, egress/SSRF deployment layer, secret custody. `.env.example` documents both new keys. |

## Verification (all re-run after every change)

| Gate | Result |
|------|--------|
| ruff (app+tests) / mypy | clean (97 files) |
| pytest | **131 passed** (new `test_phase6_hardening.py` ×6: seal/unseal/tamper/legacy/kms-raise, DNS-refusal incl. localhost→loopback, sealed-at-rest API flow + rotation, headers, boot-refusal matrix) |
| SMOKE5 live (sealed-secret path end-to-end) | SMOKE5_OK — 15 steps, receiver-side HMAC still verifies with encrypted storage |
| Playwright journey | 18 passed (see phase-6 note; run against current build) |
| Prod web server headers | verified live via `next start` :3001 (CSP/nosniff/frame/referrer present; login 200) |

## Honest limits / remaining deployment work (outside the repo)

- Provisioning KMS custody for SECRET_ENCRYPTION_KEY, managed-PITR, and
  the egress proxy are deployment acts the runbooks describe; the product
  side fails closed without them.
- Key rotation = re-seal (rotate endpoints); automatic re-wrap deferred —
  no half-tested rotation ceremony is claimed.
- `kms:v1:` resolution is a raise-by-design seam; wiring a specific cloud
  provider is deployment code, kept out of the local build on purpose.

Charter status after this phase: Phases 0–6 complete.
