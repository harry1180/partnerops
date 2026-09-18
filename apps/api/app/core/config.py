"""Core application settings loaded from environment (.env at repo root).

Secrets never have production-safe defaults: in non-local environments the
SECRET_KEY placeholder raises at import time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_repo_root() -> Path:
    # config.py lives at <root>/apps/api/app/core/ — walk up to the first
    # ancestor that actually contains .env (robust if dirs move).
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / ".env").is_file() or (cand / "pnpm-workspace.yaml").is_file():
            return cand
    return here.parents[4]


_REPO_ROOT = _find_repo_root()
_PLACEHOLDER = "dev-only-secret-change-me-8f14e45fceea167a"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _REPO_ROOT / "apps" / "api" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Cloud PartnerOps API"
    app_env: str = Field(default="local", alias="APP_ENV")
    version: str = "0.1.0"
    secret_key: str = Field(default=_PLACEHOLDER, alias="SECRET_KEY")
    # dedicated operational key for sealed tenant secrets (webhook signing
    # secrets; see app/core/secretbox.py). Falls back to a derivation of
    # secret_key in local/test only.
    secret_encryption_key: str = Field(default="", alias="SECRET_ENCRYPTION_KEY")
    # comma list of exact hosts served by this deployment ("*" local default)
    trusted_hosts: str = Field(default="*", alias="TRUSTED_HOSTS")

    database_url: str = Field(default="", alias="DATABASE_URL")
    database_url_migrate: str = Field(default="", alias="DATABASE_URL_MIGRATE")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    object_storage_endpoint: str = Field(default="http://localhost:9000", alias="OBJECT_STORAGE_ENDPOINT")
    object_storage_bucket: str = Field(default="partnerops-raw", alias="OBJECT_STORAGE_BUCKET")
    object_storage_access_key: str = Field(default="minioadmin", alias="OBJECT_STORAGE_ACCESS_KEY")
    object_storage_secret_key: str = Field(default="minioadmin", alias="OBJECT_STORAGE_SECRET_KEY")
    object_storage_use_ssl: bool = Field(default=False, alias="OBJECT_STORAGE_USE_SSL")

    cors_origins: str = Field(default="http://localhost:3000", alias="CORS_ORIGINS")
    session_cookie_name: str = Field(default="cpo_session", alias="SESSION_COOKIE_NAME")
    csrf_cookie_name: str = Field(default="cpo_csrf", alias="CSRF_COOKIE_NAME")
    access_token_ttl_minutes: int = Field(default=720, alias="ACCESS_TOKEN_TTL_MINUTES")
    login_rate_limit_per_minute: int = Field(default=10, alias="LOGIN_RATE_LIMIT_PER_MINUTE")

    seed_demo_password: str = Field(default="Demo-Passw0rd-2026", alias="SEED_DEMO_PASSWORD")
    calc_engine_version: str = Field(default="0.1.0", alias="CALC_ENGINE_VERSION")
    approval_threshold_default: str = Field(default="5000", alias="APPROVAL_THRESHOLD_DEFAULT")

    access_log_level: str = Field(default="info", alias="ACCESS_LOG_LEVEL")

    @field_validator("database_url")
    @classmethod
    def _default_db_url(cls, v: str) -> str:
        if v:
            return v
        return "postgresql+psycopg://partnerops:cpo-app@127.0.0.1:5432/partnerops"

    @property
    def is_local(self) -> bool:
        return self.app_env in ("local", "test")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_boot(self) -> None:
        if not self.is_local and self.secret_key == _PLACEHOLDER:
            raise RuntimeError(
                "SECRET_KEY is still the development placeholder while APP_ENV="
                f"{self.app_env!r}. Refusing to start with an insecure secret."
            )
        if not self.is_local and not self.secret_encryption_key:
            raise RuntimeError(
                "SECRET_ENCRYPTION_KEY must be set outside local/test — tenant "
                "secrets must not ride on a key derived from session signing."
            )
        if not self.is_local and self.trusted_hosts.strip() in ("", "*"):
            raise RuntimeError(
                "TRUSTED_HOSTS must list the exact deployment hostnames outside "
                "local/test (host-header protection).")
        if self.secret_encryption_key:
            try:
                Fernet(self.secret_encryption_key.encode())
            except Exception as exc:  # noqa: BLE001 - config clarity error
                raise RuntimeError(
                    "SECRET_ENCRYPTION_KEY must be a urlsafe Fernet key "
                    f"(generate: python -c \"from app.core.secretbox import "
                    f"generate_key; print(generate_key())\"); got: {exc}"
                ) from None


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_for_boot()
    return s
