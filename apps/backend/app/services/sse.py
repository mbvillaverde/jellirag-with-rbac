"""SSE re-framing (capability: rag-chat, tasks 6.4 + 6.5).

The broker `/llm-stream` relays the Workers AI SSE stream verbatim. FastAPI
buffers across `reader.read()` boundaries, parses each complete `data:` event,
extracts the token text, and re-emits strict `data: <json>\n\n` events with a
terminal `data: [DONE]\n\n`. This replaces the prior swallow-catch with a
proper buffer.

Workers AI streaming emits OpenAI-style chunks (`choices[0].delta.content`) and
may also emit Cloudflare's `{response}` shape; both are handled.
"""
from __future__ import annotations

import json
from typing import Any, AsyncIterator


def _extract_token(payload: Any) -> str:
    if isinstance(payload, dict):
        if isinstance(payload.get("response"), str):
            return payload["response"]
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            if isinstance(content, str):
                return content
        # Some upstreams put the token under "token" or a nested "response".
        if isinstance(payload.get("token"), str):
            return payload["token"]
    return ""


async def reframe_sse(byte_iter: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Consume raw broker bytes; yield strict `data:` SSE lines.

    The final `data: [DONE]` is emitted by us when the upstream stream ends,
    guaranteeing a clean terminal event even if the upstream omits it.
    """
    buffer = ""
    async for chunk in byte_iter:
        buffer += chunk.decode("utf-8", errors="replace")
        # SSE events are separated by a blank line.
        while "\n\n" in buffer:
            event, buffer = buffer.split("\n\n", 1)
            for line in event.split("\n"):
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if payload == "[DONE]":
                    continue
                try:
                    token = _extract_token(json.loads(payload))
                except Exception:
                    continue
                if token:
                    yield f"data: {json.dumps({'response': token})}\n\n"
    yield "data: [DONE]\n\n"
