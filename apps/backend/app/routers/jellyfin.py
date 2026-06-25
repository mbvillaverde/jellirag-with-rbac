"""Ephemeral Jellyfin image proxy (capability: rich-source-citations).

Streams Jellyfin primary images through to the client without caching, logging,
or persistence. `Cache-Control: no-store` is set on every response so browsers
do not retain the image either — the endpoint is intentionally re-fetched each
time it appears, matching the homelab privacy-first architecture.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from ..security.deps import Principal, require_role
from ..services.jellyfin_client import JellyfinClient, JellyfinUnreachable

router = APIRouter(tags=["jellyfin"])


def _get_jellyfin(request: Request) -> JellyfinClient:
    return request.app.state.jellyfin


@router.get("/jellyfin/image")
async def get_image(
    request: Request,
    id: str | None = Query(None, description="Jellyfin item id"),
    principal: Principal = Depends(require_role("member")),
    jellyfin: JellyfinClient = Depends(_get_jellyfin),
):
    if not id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing id parameter")

    try:
        upstream = await jellyfin.primary_image(id)
    except JellyfinUnreachable as exc:
        # Jellyfin down / off-Tailnet / timeout → 502 to trigger frontend fallback.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))

    if upstream.status_code == 404:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image not found")
    if upstream.status_code >= 400:
        # Any other upstream failure (5xx, auth, etc.) surfaces as 502.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Jellyfin returned {upstream.status_code}",
        )

    content_type = upstream.headers.get("content-type", "image/jpeg")
    return Response(
        content=upstream.content,
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
