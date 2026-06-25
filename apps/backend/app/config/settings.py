"""Application configuration loaded from environment.

All AI providers are OpenAI-compatible HTTP endpoints. No Cloudflare credentials are present.
All data is stored in a local SQLite database with sqlite-vec for vector search.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- AI providers (OpenAI-compatible) ----
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    embed_base_url: str
    embed_api_key: str
    embed_model: str
    embed_dim: int = 768
    llm_timeout_seconds: int = 5

    # ---- database ----
    sqlite_path: str = "/var/jellirag/jellyrag.db"

    # ---- sync service ----
    sync_embed_concurrency: int = 4

    # ---- homelab overlay (Tailscale-only) ----
    jellyfin_tailscale_url: str
    jellyfin_api_key: str
    jellyfin_deeplink_base: str

    # ---- conversation lifecycle ----
    session_ttl_days: int = 30  # 0 disables pruning (retain forever)

    # ---- auth perimeter ----
    jwt_secret: str
    jwt_ttl_days: int = 7
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    login_max_attempts: int = 10
    login_window_seconds: int = 600

    # ---- CORS / sync scheduling ----
    frontend_origin: str = ""  # comma-separated origins also accepted via list parse
    frontend_origins: list[str] | None = None
    sync_cron: str = "0 3 * * *"

    @property
    def allowed_origins(self) -> list[str]:
        if self.frontend_origins:
            return self.frontend_origins
        if self.frontend_origin:
            return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
