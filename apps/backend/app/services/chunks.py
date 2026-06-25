"""Chunk synthesis + content hashing (capability: library-sync).

Builds a normalized `chunk_text` from a Jellyfin item and a `sha256` content
hash used by the incremental two-way diff. Full text lives in SQLite with
vector metadata in vec_chunks.

Parsing of `People`/`Genres` is defensive (shapes vary across Jellyfin
versions): actors are filtered by `Type == "Actor"` and capped, missing fields
are tolerated. `chunk_text` is bounded to the embedding model's ~512-token
input (~2KB) so embed calls never receive oversized input.
"""
from __future__ import annotations

import hashlib
from typing import Any

MAX_CHUNK_CHARS = 2048  # ~512 tokens @ ~4 chars/token (nomic-embed-text input)


def _genres(item: dict[str, Any]) -> str:
    raw = item.get("Genres") or item.get("GenreItems") or []
    names: list[str] = []
    for x in raw:
        if isinstance(x, str):
            names.append(x)
        elif isinstance(x, dict):
            name = x.get("Name")
            if name:
                names.append(str(name))
    return ", ".join(n for n in names if n)


def _cast(item: dict[str, Any], cap: int = 8) -> str:
    people = item.get("People") or []
    actors: list[str] = []
    for p in people:
        if isinstance(p, dict) and p.get("Type") == "Actor":
            name = p.get("Name")
            if name:
                actors.append(str(name))
            if len(actors) >= cap:
                break
    return ", ".join(actors)


def synthesize_chunk_text(item: dict[str, Any]) -> str:
    title = item.get("Name") or item.get("Title") or "Untitled"
    year = item.get("ProductionYear")
    genres = _genres(item)
    cast = _cast(item)
    overview = (item.get("Overview") or "").strip()

    parts: list[str] = [str(title)]
    if year:
        parts.append(f"({year})")
    if genres:
        parts.append(f"Genres: {genres}.")
    if cast:
        parts.append(f"Cast: {cast}.")
    if overview:
        parts.append(overview)

    text = " ".join(parts)
    # Defensive bound so embed never receives oversized input.
    if len(text) > MAX_CHUNK_CHARS:
        text = text[: MAX_CHUNK_CHARS - 1].rstrip() + "\u2026"
    return text


def content_hash(chunk_text: str) -> str:
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()


def item_to_chunk(item: dict[str, Any]) -> dict[str, Any]:
    text = synthesize_chunk_text(item)
    return {
        "jf_id": str(item.get("Id")),
        "title": str(item.get("Name") or item.get("Title") or "Untitled"),
        "year": item.get("ProductionYear"),
        "genres": _genres(item),
        "cast": _cast(item),
        "overview": (item.get("Overview") or "").strip(),
        "chunk_text": text,
        "content_hash": content_hash(text),
    }
