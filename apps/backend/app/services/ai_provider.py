"""OpenAI-compatible AI provider clients for LLM and embeddings.

Two independent HTTP clients, each configured by {BASE_URL, API_KEY, MODEL} env vars.
Supports any OpenAI-compatible provider: Ollama, Groq, OpenAI, vLLM, LM Studio, etc.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)


class AIProviderError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"provider {status}: {message}")
        self.status = status
        self.message = message


class LLMClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, model: str, timeout: int = 5) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._timeout = httpx.Timeout(connect=timeout, read=None, write=timeout, pool=timeout)

    async def stream_chat(self, messages: list[dict[str, Any]], max_tokens: int) -> AsyncIterator[str]:
        resp = await self._client.post(
            f"{self._base}/chat/completions",
            json={"messages": messages, "model": self._model, "max_tokens": max_tokens, "stream": True},
            headers={**self._headers, "Accept": "text/event-stream"},
            timeout=self._timeout,
        )
        if resp.status_code >= 400:
            raise AIProviderError(resp.status_code, _safe_text(resp))

        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n\n" in buffer:
                line, buffer = buffer.split("\n\n", 1)
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    return
                try:
                    parsed = _safe_json(data)
                    if "choices" in parsed and parsed["choices"]:
                        delta = parsed["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                except Exception:
                    continue

    async def warmup(self) -> None:
        try:
            async for _ in self.stream_chat([{"role": "user", "content": "hi"}], 1):
                break  # just need the first token
            log.info("LLM warmup successful")
        except Exception as e:
            log.warning("LLM warmup failed (non-blocking): %s", e)


class EmbeddingsClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
        concurrency: int = 4,
    ) -> None:
        self._client = client
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._model = model
        self._sem = asyncio.Semaphore(concurrency)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        
        async def _call() -> list[list[float]]:
            resp = await self._client.post(
                f"{self._base}/embeddings",
                json={"model": self._model, "input": texts},
                headers=self._headers,
                timeout=30.0,
            )
            if resp.status_code == 429:
                raise AIProviderError(429, "rate limited")
            if resp.status_code >= 400:
                raise AIProviderError(resp.status_code, _safe_text(resp))
            data = resp.json()
            return [item["embedding"] for item in data.get("data", [])]

        try:
            async with self._sem:
                return await _call()
        except AIProviderError as e:
            if e.status == 429:
                await asyncio.sleep(2 ** min(4, 3))  # exponential backoff up to 8s
                async with self._sem:
                    return await _call()
            raise


def _safe_text(resp: httpx.Response) -> str:
    try:
        return resp.json().get("error", resp.text)
    except Exception:
        return resp.text


def _safe_json(text: str) -> dict[str, Any]:
    import json
    return json.loads(text)