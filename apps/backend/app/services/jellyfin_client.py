"""Jellyfin client — reaches the homelab ONLY over the Tailscale overlay.

Uses the shared `httpx.AsyncClient`. Failures are surfaced as
`JellyfinUnreachable` so the sync service can fail fast with a descriptive
error instead of partially mutating Vectorize/D1.
"""
from __future__ import annotations

from typing import Any

import httpx


class JellyfinUnreachable(RuntimeError):
    pass


class JellyfinClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"X-Emby-Token": api_key, "Accept": "application/json"}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        try:
            resp = await self._client.get(url, params=params, headers=self._headers, timeout=20.0)
        except httpx.HTTPError as exc:
            raise JellyfinUnreachable(
                f"Jellyfin unreachable at {self._base} (Tailscale). {exc}"
            ) from exc
        if resp.status_code == 401:
            raise JellyfinUnreachable("Jellyfin rejected the API key (401).")
        if resp.status_code >= 400:
            raise JellyfinUnreachable(f"Jellyfin returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def library_items(self) -> list[dict[str, Any]]:
        """Movies + Series with the fields the chunk synthesizer needs."""
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": "Overview,Genres,People,ProductionYear",
        }
        data = await self._get("/Items", params=params)
        return data.get("Items", []) if isinstance(data, dict) else []

    async def primary_image(self, jf_id: str) -> httpx.Response:
        """Fetch the primary image for an item as a raw binary response.

        Returns the unprocessed `httpx.Response` so the caller can map upstream
        status codes (e.g. 404 = no primary image vs. 5xx = upstream failure)
        without conflating them. Only network/timeout errors raise
        `JellyfinUnreachable`; HTTP error status codes are returned as-is.
        """
        # URL-encode the item id defensively (Jellyfin ids are GUID-safe, but
        # never trust caller input to be path-segment clean).
        from urllib.parse import quote

        path = f"/Items/{quote(jf_id, safe='')}/Images/Primary"
        url = f"{self._base}{path}"
        params = {"maxWidth": "200", "maxHeight": "300", "quality": "90"}
        try:
            return await self._client.get(
                url, params=params, headers=self._headers, timeout=20.0
            )
        except httpx.HTTPError as exc:
            raise JellyfinUnreachable(
                f"Jellyfin unreachable at {self._base} (Tailscale). {exc}"
            ) from exc

    async def check_reachable(self) -> None:
        """Fail fast with a descriptive error if the homelab is unreachable."""
        try:
            resp = await self._client.get(f"{self._base}/System/Info/Public", headers=self._headers, timeout=10.0)
        except httpx.HTTPError as exc:
            raise JellyfinUnreachable(
                f"Jellyfin unreachable at {self._base} (Tailscale). {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise JellyfinUnreachable(f"Jellyfin returned {resp.status_code} on reachability check.")
