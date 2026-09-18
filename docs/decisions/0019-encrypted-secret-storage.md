# ADR-0019: Encrypted-at-rest secrets with a dedicated operational key

Date: 2026-09-18
Status: accepted
Phase: 6

## Context

Tenant webhook signing secrets were stored (Phase 5) as `secret_ref =
local:v1:<plaintext>` — acceptable for a local demo, unacceptable for
production: any read of `webhook_endpoints` (replica, dump, mis-scoped
grant) exposes the values used to authenticate our event deliveries.
A KMS round-trip is the cloud-native answer, but the local build must
stay dependency-free, and "kms:v1:<ref>" would be a fake until a real
provider is configured.

## Decision

`app/core/secretbox.py`: Fernet symmetric encryption (AES-128-CBC +
HMAC, `cryptography`) under **SECRET_ENCRYPTION_KEY**, a dedicated
operational key separate from SECRET_KEY (session signing). Sealed
values are self-contained tokens stored in the owning row's `secret_ref`:

    enc:v1:<fernet>    sealed at rest — the default, local and prod
    kms:v1:<ref>       KMS-provided — resolved by a configured provider;
                       unconfigured deployments RAISE, never fake success
    local:v1:<plain>   pre-hardening rows only, readable until rotated

Boot validation (production/staging): SECRET_ENCRYPTION_KEY required and
must be a valid Fernet key; TRUSTED_HOSTS must list exact hostnames;
placeholder SECRET_KEY already refused. Local/test without an explicit
key derives one from SECRET_KEY with a loud startup warning.

Consequences spelled out honestly:
- Key rotation invalidates sealed secrets → webhook endpoints get a
  Rotate-secret action (new secret shown once, receiver updates).
- The DB alone is not a secret store leak anymore; the key is the secret.
  Key custody (KMS/HSM/secret manager for the Fernet key itself) is a
  deployment concern — the key arrives via environment, exactly like a
  KMS-issued data key.
- Column-level encryption does not hide metadata (which endpoints exist,
  URLs); full-disk/volume encryption remains the platform baseline.

## Rejected alternatives

- Store only a random UUID in secret_ref + plaintext in a separate table:
  one compromised read path still exposes everything, and RLS on a
  platform-owned table would block the app role from its own writes
  without a service role — more machinery, less defense.
- Asymmetric per-tenant keypairs: overkill — receivers verify HMAC, not
  signatures.
