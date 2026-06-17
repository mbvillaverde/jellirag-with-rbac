"""Typed client for the broker Worker.

All Cloudflare-bound operations funnel through this object. It uses a single
shared `httpx.AsyncClient` (created once in the app lifespan) and authenticates
every request with `X-Broker-Secret`. The client makes no policy decisions; it
translates Python calls into the broker's domain surface and raises
`BrokerError` on non-2xx so routers can map to HTTP status codes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx


class BrokerError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"broker {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class PreparedRag:
    matches: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    history: list[dict[str, Any]]


class BrokerClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, secret: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"X-Broker-Secret": secret}

    async def _post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        resp = await self._client.post(
            f"{self._base}{path}", json=payload, headers=self._headers, timeout=30.0
        )
        if resp.status_code >= 400:
            raise BrokerError(resp.status_code, _safe_text(resp))
        return resp.json()

    async def prepare_rag(
        self,
        *,
        session_id: str,
        owner_email: str,
        message: str,
        top_k: int,
        history_max_tokens: int,
    ) -> PreparedRag:
        data = await self._post(
            "/prepare-rag",
            {
                "session_id": session_id,
                "owner_email": owner_email,
                "message": message,
                "top_k": top_k,
                "history_max_tokens": history_max_tokens,
            },
        )
        return PreparedRag(
            matches=data.get("matches", []),
            chunks=data.get("chunks", []),
            history=data.get("history", []),
        )

    async def search(self, text: str, top_k: int = 8, filter: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {"text": text, "topK": top_k}
        if filter is not None:
            payload["filter"] = filter
        data = await self._post("/search", payload)
        return data.get("matches", [])

    async def embed(self, texts: list[str]) -> list[list[float]]:
        data = await self._post("/embed", {"texts": texts})
        return data.get("vectors", [])

    async def chunks(self, jf_ids: list[str]) -> list[dict[str, Any]]:
        data = await self._post("/chunks", {"jf_ids": jf_ids})
        return data.get("chunks", [])

    async def llm_stream(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> httpx.Response:
        """Stream the model output. Caller iterates `resp.aiter_raw()`."""
        resp = await self._client.post(
            f"{self._base}/llm-stream",
            json={"messages": messages, "max_tokens": max_tokens},
            headers={**self._headers, "Accept": "text/event-stream"},
            timeout=httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0),
        )
        if resp.status_code >= 400:
            raise BrokerError(resp.status_code, _safe_text(resp))
        return resp

    async def history_read(self, session_id: str, owner_email: str, max_tokens: int) -> list[dict[str, Any]]:
        data = await self._post(
            "/history/read",
            {"session_id": session_id, "owner_email": owner_email, "max_tokens": max_tokens},
        )
        return data.get("history", [])

    async def history_append(
        self,
        *,
        session_id: str,
        owner_email: str,
        role: str,
        content: str,
        token_count: int = 0,
    ) -> None:
        await self._post(
            "/history/append",
            {
                "session_id": session_id,
                "owner_email": owner_email,
                "role": role,
                "content": content,
                "token_count": token_count,
            },
        )

    async def ingest_upsert(self, items: list[dict[str, Any]]) -> int:
        data = await self._post("/ingest/upsert", {"items": items})
        return int(data.get("upserted", 0))

    async def ingest_delete(self, jf_ids: list[str]) -> int:
        data = await self._post("/ingest/delete", {"jf_ids": jf_ids})
        return int(data.get("deleted", 0))

    async def sync_state_get(self) -> list[dict[str, Any]]:
        resp = await self._client.get(f"{self._base}/sync/state", headers=self._headers, timeout=30.0)
        if resp.status_code >= 400:
            raise BrokerError(resp.status_code, _safe_text(resp))
        return resp.json().get("items", [])

    async def sync_state_put(self, items: list[dict[str, Any]]) -> int:
        resp = await self._client.put(
            f"{self._base}/sync/state", json={"items": items}, headers=self._headers, timeout=30.0
        )
        if resp.status_code >= 400:
            raise BrokerError(resp.status_code, _safe_text(resp))
        return int(resp.json().get("upserted", 0))

    async def sessions_prune(self, older_than: str) -> dict[str, int]:
        data = await self._post("/sessions/prune", {"older_than": older_than})
        return {
            "deleted_sessions": int(data.get("deleted_sessions", 0)),
            "deleted_messages": int(data.get("deleted_messages", 0)),
        }

    # ---- users/* (broker stores opaque hashes; never verifies passwords) ----
    async def users_lookup(self, email: str) -> dict[str, Any] | None:
        data = await self._post("/users/lookup", {"email": email})
        return data.get("user")

    async def users_create(self, email: str, role: str, pw_hash: str) -> None:
        await self._post("/users/create", {"email": email, "role": role, "pw_hash": pw_hash})

    async def users_list(self) -> list[dict[str, Any]]:
        data = await self._post("/users/list", {})
        return data.get("users", [])

    async def users_update(
        self, email: str, *, role: str | None = None, pw_hash: str | None = None
    ) -> None:
        payload: dict[str, Any] = {"email": email}
        if role is not None:
            payload["role"] = role
        if pw_hash is not None:
            payload["pw_hash"] = pw_hash
        await self._post("/users/update", payload)

    async def users_delete(self, email: str) -> dict[str, int]:
        data = await self._post("/users/delete", {"email": email})
        return {
            "deleted_users": int(data.get("deleted_users", 0)),
            "deleted_sessions": int(data.get("deleted_sessions", 0)),
            "deleted_messages": int(data.get("deleted_messages", 0)),
        }


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", resp.text)
    except Exception:
        return resp.text
