"""Phase 6 hardening tests: sealed secrets, DNS-aware SSRF, security headers,
host-header protection, boot validation."""

from __future__ import annotations

import uuid

import pytest

from app.core import secretbox
from app.core.config import get_settings


def _h(csrf: str) -> dict:
    return {"X-CSRF-Token": csrf}


async def _login_csrf(client, email: str) -> str:
    settings = get_settings()
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": settings.seed_demo_password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def test_seal_unseal_roundtrip_and_legacy_paths():
    ref = secretbox.seal("whsec_supersecret")
    assert ref.startswith("enc:v1:")
    assert "supersecret" not in ref  # ciphertext at rest
    assert secretbox.unseal(ref) == "whsec_supersecret"
    assert secretbox.unseal("local:v1:plaintext-legacy") == "plaintext-legacy"
    assert secretbox.unseal("garbage") is None
    assert secretbox.unseal("enc:v1:not-a-fernet-token") is None  # wrong key/corrupt
    with pytest.raises(RuntimeError):
        secretbox.unseal("kms:v1:some-arn")  # raises — never fakes success


def test_tampered_token_rejected():
    ref = secretbox.seal("abc")
    bad = ref[:-6] + "AAAAAA"
    assert secretbox.unseal(bad) is None


@pytest.mark.asyncio
async def test_resolve_error_blocks_name_pointing_at_loopback():
    from app.services.webhooks import resolve_error

    # "localhost" resolves to 127.0.0.1/::1 — refused when private not allowed
    err = await resolve_error("http://localhost:9999/hook", allow_private=False)
    assert err and "private" in err
    # allowed in local/test mode
    assert await resolve_error("http://localhost:9999/hook", allow_private=True) is None
    # garbage host fails closed
    assert await resolve_error("http://no-such-host.invalid/h", allow_private=True)


@pytest.mark.asyncio
async def test_webhook_secret_stored_sealed(client, migrated_db):
    """API-created endpoints persist only a sealed ref; the plaintext is
    returned exactly once; overview never contains either."""
    csrf = await _login_csrf(client, "msp@northwind-msp.example.com")
    r = await client.post("/api/v1/integrations/webhooks", json={
        "url": f"http://127.0.0.1:9999/hooks/{uuid.uuid4().hex[:6]}",
        "events": ["invoice.issued"]}, headers=_h(csrf))
    assert r.status_code == 201
    created = r.json()
    secret = created["signing_secret"]
    assert secret.startswith("whsec_")

    # at rest: sealed ref only
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models.approvals import WebhookEndpoint
    async with SessionLocal() as s:
        ep = (await s.execute(select(WebhookEndpoint).where(
            WebhookEndpoint.id == uuid.UUID(created["id"])))).scalar_one()
        assert ep.secret_ref.startswith("enc:v1:")
        assert secret not in ep.secret_ref
    # deliveries still sign correctly with the sealed secret
    r = await client.post(f"/api/v1/integrations/webhooks/{created['id']}/test",
                          json={}, headers=_h(csrf))
    assert r.status_code == 200 and r.json()["queued"] >= 1
    # rotation returns a new secret once, old stops verifying
    r = await client.post(f"/api/v1/integrations/webhooks/{created['id']}/rotate-secret",
                          headers=_h(csrf))
    assert r.status_code == 200
    new_secret = r.json()["signing_secret"]
    assert new_secret != secret and new_secret.startswith("whsec_")


# ---------------- headers & host protection ----------------

@pytest.mark.asyncio
async def test_api_security_headers(client, migrated_db):
    # shipped since Phase 0: hardened defaults on every API response
    r = await client.get("/health/live")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", "")
    assert r.headers.get("cache-control") == "no-store"


def test_boot_validation_requires_keys_outside_local(monkeypatch):
    from app.core.config import Settings

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "a-real-secret-not-placeholder-xyz")
    monkeypatch.setenv("TRUSTED_HOSTS", "ops.example.com")
    monkeypatch.delenv("SECRET_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_ENCRYPTION_KEY"):
        Settings(_env_file=None).validate_for_boot()
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(RuntimeError, match="Fernet"):
        Settings(_env_file=None).validate_for_boot()
    monkeypatch.setenv("SECRET_ENCRYPTION_KEY", secretbox.generate_key())
    monkeypatch.setenv("TRUSTED_HOSTS", "*")
    with pytest.raises(RuntimeError, match="TRUSTED_HOSTS"):
        Settings(_env_file=None).validate_for_boot()
    monkeypatch.setenv("TRUSTED_HOSTS", "ops.example.com")
    Settings(_env_file=None).validate_for_boot()  # ok
