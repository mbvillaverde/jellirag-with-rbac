"""Context-budget manager (capability: rag-chat, task 6.1).

Conservative budgets summing to a cap below the model's effective window.
After /prepare-rag returns chunks + a history window, FastAPI reconciles:
trim oldest history first based on actual chunk sizes so retrieved context and
response headroom are preserved.

Token estimator: char-based heuristic (~4 chars/token). Fast, dependency-free,
slightly conservative — safe for budgeting. Upgrade path: swap in a real
Llama tokenizer if drift causes truncation.
"""
from __future__ import annotations

from dataclasses import dataclass

# Conservative usable input budget. The non-fast llama-3.1-8b-instruct lists a
# 7,968-token window; the -fast variant's exact cap is pending (OQ-1), so we
# stay well below it. CONTEXT_CAP - RESPONSE_HEADROOM is the room for prompt.
CONTEXT_CAP = 6000
RESPONSE_HEADROOM = 768  # also the explicit max_tokens sent to /llm-stream (task 6.9)
SYSTEM_RESERVE = 512  # prompt template + link instructions headroom
# How much history we ask the broker to return (FastAPI reconciles further).
HISTORY_REQUEST_BUDGET = 1500


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass
class AssembledContext:
    messages: list[dict[str, str]]
    contributing_jf_ids: list[str]
    history_dropped: int


def reconcile_and_assemble(
    *,
    system_prompt: str,
    history: list[dict],
    chunks: list[dict],
    user_message: str,
) -> AssembledContext:
    """Trim oldest history first; preserve retrieved context + response headroom."""
    system_tokens = estimate_tokens(system_prompt)
    chunk_text = "\n".join(c.get("chunk_text", "") for c in chunks)
    chunk_tokens = estimate_tokens(chunk_text)
    user_tokens = estimate_tokens(user_message)

    available_for_history = (
        CONTEXT_CAP - RESPONSE_HEADROOM - system_tokens - chunk_tokens - user_tokens
    )

    history = list(history)  # chronological, oldest -> newest
    kept: list[dict] = []
    history_tokens = 0
    # Walk oldest-first, including until the budget is exhausted.
    for turn in history:
        t = estimate_tokens(turn.get("content", ""))
        if history_tokens + t > available_for_history:
            break
        kept.append(turn)
        history_tokens += t

    dropped = len(history) - len(kept)

    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for turn in kept:
        messages.append({"role": turn.get("role", "user"), "content": turn.get("content", "")})

    if chunk_text.strip():
        messages.append(
            {
                "role": "system",
                "content": "Relevant items from the user's media library:\n\n" + chunk_text,
            }
        )
    messages.append({"role": "user", "content": user_message})

    contributing = [c.get("jf_id") for c in chunks if c.get("jf_id")]
    return AssembledContext(messages=messages, contributing_jf_ids=contributing, history_dropped=dropped)
