"""Encrypted secret storage for tenant secrets (webhook signing secrets).

Phase 6 hardening (ADR-0019). Design:

- Secret values are sealed with Fernet (AES-128-CBC + HMAC) keyed by
  settings.secret_encryption_key — a dedicated operational key, separate
  from SECRET_KEY (session signing). Sealed tokens are self-contained and
  live in the owning row's `secret_ref` column: a database dump (or a
  tenant reading its own rows) yields ciphertext, useless without the key.
- secret_ref encodes provenance so ops can migrate backends:
    enc:v1:<fernet>    sealed at rest (current default)
    kms:v1:<ref>       KMS-resolved (deployment seam; unresolvable here
                       by design — resolve raises, it never pretends)
    local:v1:<plain>   pre-hardening rows; readable until rotated away.
- Key handling: if SECRET_ENCRYPTION_KEY is unset in local/test, a key is
  derived from SECRET_KEY (deterministic dev experience; logged once as a
  warning). In production validate_for_boot() requires the explicit key.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

log = logging.getLogger(__name__)

PREFIX_ENC = "enc:v1:"
PREFIX_KMS = "kms:v1:"
PREFIX_LEGACY = "local:v1:"

_warned_derive = False


def generate_key() -> str:
    """Fresh urlsafe Fernet key (for .env provisioning)."""
    return Fernet.generate_key().decode()


def _key_bytes() -> bytes:
    global _warned_derive
    settings = get_settings()
    key = settings.secret_encryption_key
    if key:
        return key.encode()
    if not settings.is_local:
        raise RuntimeError("SECRET_ENCRYPTION_KEY is required outside local/test")
    if not _warned_derive:
        log.warning("SECRET_ENCRYPTION_KEY unset — deriving from SECRET_KEY "
                    "(local/test only). Rotate SECRET_KEY and every sealed "
                    "secret becomes unreadable; set the explicit key in "
                    "deployments.")
        _warned_derive = True
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_key_bytes())


def seal(value: str) -> str:
    """Returns a secret_ref carrying the encrypted value."""
    return PREFIX_ENC + _fernet().encrypt(value.encode()).decode()


def unseal(ref: str | None) -> str | None:
    """Resolve a secret_ref to its plaintext, or None when unresolvable
    (bad key, corrupt token, unknown scheme)."""
    if not ref:
        return None
    if ref.startswith(PREFIX_ENC):
        try:
            return _fernet().decrypt(ref[len(PREFIX_ENC):].encode()).decode()
        except (InvalidToken, ValueError):
            return None
    if ref.startswith(PREFIX_LEGACY):
        return ref[len(PREFIX_LEGACY):]
    if ref.startswith(PREFIX_KMS):
        raise RuntimeError(
            f"kms secret provider not configured for this deployment ({ref[:24]}…)")
    return None


def is_sealed(ref: str | None) -> bool:
    return bool(ref and ref.startswith(PREFIX_ENC))
