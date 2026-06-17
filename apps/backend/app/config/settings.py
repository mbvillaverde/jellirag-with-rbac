"""Application configuration loaded from Dokploy encrypted environment.

No Cloudflare credential lives here (or on the VPS). Only the Jellyfin key, the
single broker pre-shared secret, the deeplink base, the JWT secret, and the
one-shot bootstrap-admin env are present at runtime.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # ---- broker boundary (the only path to Cloudflare) ----
    broker_url: str
    broker_secret: str

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
