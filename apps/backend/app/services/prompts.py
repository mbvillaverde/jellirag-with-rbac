"""System-prompt + deep-link construction (capability: rag-chat, task 7.1).

Injects `JELLYFIN_DEEPLINK_BASE` so the LLM emits click-to-play links rooted at
the Tailscale/MagicDNS base using the item's `jf_id`. Links fail closed: off the
Tailnet the hostname does not resolve (NXDOMAIN), so no homelab port is exposed.

The exact deep-link path is version-dependent (OQ-5): `/web/index.html#/details?id=`
-- verify against the operator's Jellyfin before finalizing (task 7.2). The
template below uses the current Jellyfin 10.x path.
"""
from __future__ import annotations


def deep_link(deeplink_base: str, jf_id: str) -> str:
    base = deeplink_base.rstrip("/")
    return f"{base}/web/index.html#/details?id={jf_id}"


def build_system_prompt(deeplink_base: str) -> str:
    base = deeplink_base.rstrip("/")
    return (
        "You are JellieRAG, a friendly assistant that helps users discover and "
        "contextualize movies and series in their personal Jellyfin media library. "
        "Answer using the provided library context; if nothing is relevant, say so.\n\n"
        f"When you recommend a specific item, link to it using its jf_id with this "
        f"base URL: {base}/web/index.html#/details?id=<jf_id> . Render the link as "
        "Markdown. These links resolve only when the reader is connected to the "
        "Tailscale network; you may note that deep links require Tailscale.\n\n"
        "Be concise. Prefer a few concrete recommendations with a one-line reason each."
    )
