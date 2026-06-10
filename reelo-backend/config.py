"""Application configuration.

Single source of truth for env-driven settings, loaded via pydantic-settings.
Import the cached singleton with :func:`get_settings`; do not read ``os.environ``
elsewhere in the codebase.

All secret-bearing fields tolerate being empty in ``dev`` so the app can import
and boot (endpoints still return 501) without a fully provisioned environment.
Helpers (e.g. :meth:`Settings.master_key_bytes`) raise only when the secret is
actually needed.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "prod"]
StorageBackend = Literal["local", "s3"]


class Settings(BaseSettings):
    """Typed view over the process environment / ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----------------------------------------------------------------
    env: Environment = Field(default="dev", alias="REELO_ENV")
    debug: bool = Field(default=True, alias="REELO_DEBUG")
    base_url: str = Field(default="http://localhost:8000", alias="REELO_BASE_URL")
    cors_origins: str = Field(default="http://localhost:3000", alias="REELO_CORS_ORIGINS")

    # ---- Postgres -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://reelo:reelo@localhost:5432/reelo",
        alias="DATABASE_URL",
    )

    # ---- Redis --------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # ---- Crypto -------------------------------------------------------------
    master_key: str = Field(default="", alias="REELO_MASTER_KEY")

    # ---- Session ------------------------------------------------------------
    session_secret: str = Field(default="dev-insecure-session-secret", alias="SESSION_SECRET")
    session_cookie_name: str = Field(default="reelo_session", alias="SESSION_COOKIE_NAME")
    session_max_age: int = Field(default=1209600, alias="SESSION_MAX_AGE")

    # ---- Google OAuth -------------------------------------------------------
    google_oauth_client_id: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str = Field(default="", alias="GOOGLE_OAUTH_CLIENT_SECRET")
    google_oauth_redirect_uri: str = Field(
        default="http://localhost:8000/auth/callback", alias="GOOGLE_OAUTH_REDIRECT_URI"
    )
    oauth_post_login_redirect: str = Field(
        default="http://localhost:3000", alias="OAUTH_POST_LOGIN_REDIRECT"
    )

    # ---- Object storage -----------------------------------------------------
    storage_backend: StorageBackend = Field(default="local", alias="STORAGE_BACKEND")
    storage_local_root: str = Field(default="./.reelo-storage", alias="STORAGE_LOCAL_ROOT")
    storage_bucket: str = Field(default="reelo-assets", alias="STORAGE_BUCKET")
    storage_region: str = Field(default="us-east-1", alias="STORAGE_REGION")
    storage_endpoint_url: str = Field(default="", alias="STORAGE_ENDPOINT_URL")
    storage_access_key_id: str = Field(default="", alias="STORAGE_ACCESS_KEY_ID")
    storage_secret_access_key: str = Field(default="", alias="STORAGE_SECRET_ACCESS_KEY")
    storage_signed_url_ttl: int = Field(default=3600, alias="STORAGE_SIGNED_URL_TTL")

    # ---- Worker -------------------------------------------------------------
    worker_max_jobs: int = Field(default=10, alias="WORKER_MAX_JOBS")

    # ---- OmniVoice (voice-clone microservice) -------------------------------
    # Base URL of the Reelo-hosted OmniVoice GPU microservice (see
    # services/omnivoice/). Empty = the omnivoice voice provider is unavailable
    # (is_available() returns False; fallback/Setup hides it). E.g.
    # http://localhost:8002 or http://<gpu-host>:8002.
    omnivoice_url: str = Field(default="", alias="OMNIVOICE_URL")

    # ---- Derived / validated ------------------------------------------------
    @field_validator("cors_origins")
    @classmethod
    def _strip_origins(cls, v: str) -> str:
        return v.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins as a list (empty entries dropped)."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_prod(self) -> bool:
        return self.env == "prod"

    @property
    def storage_endpoint_url_or_none(self) -> str | None:
        return self.storage_endpoint_url or None

    def master_key_bytes(self) -> bytes:
        """Decode the base64 master key into 32 raw bytes.

        Raises:
            ValueError: if the key is missing or not a valid 32-byte key.
        """
        if not self.master_key:
            raise ValueError(
                "REELO_MASTER_KEY is not set. Generate one with: "
                'python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"'
            )
        try:
            raw = base64.b64decode(self.master_key, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("REELO_MASTER_KEY must be valid base64") from exc
        if len(raw) != 32:
            raise ValueError("REELO_MASTER_KEY must decode to exactly 32 bytes (AES-256)")
        return raw


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings`."""
    return Settings()
