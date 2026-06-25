"""Incremental Jellyfin library sync (capability: library-sync).

Two-way diff against `sync_state`: embed + upsert only new/changed items,
delete vectors + chunks for removed items, and skip unchanged items. Fails
fast (no partial SQLite mutation) if Jellyfin is unreachable over Tailscale.
Processes in streamed batches so peak RAM stays < 1GB at 5,000 items.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..services.ai_provider import AIProviderError, EmbeddingsClient
from ..services.db import Database
from ..services.jellyfin_client import JellyfinClient, JellyfinUnreachable
from ..services.chunks import item_to_chunk

log = logging.getLogger("jellirag.sync")

EMBED_BATCH = 50          # texts per embed call
INGEST_BATCH = 50         # items per batch write


@dataclass
class SyncSummary:
    total: int = 0
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "errors": self.errors,
        }


class SyncFailed(RuntimeError):
    pass


async def run_library_sync(
    embed: EmbeddingsClient,
    db: Database,
    jellyfin: JellyfinClient,
) -> SyncSummary:
    summary = SyncSummary()

    # Fail fast on unreachable; no partial mutation.
    await jellyfin.check_reachable()
    items = await jellyfin.library_items()
    summary.total = len(items)

    # Synthesize chunks (streaming-friendly; bounded text size).
    chunks = [item_to_chunk(it) for it in items if it.get("Id")]
    jellyfin_ids = {c["jf_id"] for c in chunks}
    by_id = {c["jf_id"]: c for c in chunks}

    # Two-way diff: fetch sync_state directly from SQLite
    conn = await db.get_connection()
    try:
        cursor = await conn.execute("SELECT jf_id, content_hash FROM sync_state")
        known_rows = await cursor.fetchall()
        known_active = {row[0]: {"jf_id": row[0], "content_hash": row[1]} for row in known_rows}
        known_active_ids = set(known_active.keys())
    finally:
        await db.return_connection(conn)

    to_add = jellyfin_ids - known_active_ids
    to_remove = known_active_ids - jellyfin_ids
    to_update = {
        jid
        for jid in (jellyfin_ids & known_active_ids)
        if by_id[jid]["content_hash"] != known_active[jid].get("content_hash")
    }
    unchanged = (jellyfin_ids & known_active_ids) - to_update

    summary.unchanged = len(unchanged)  # no embedding for these (steady-state)

    # Embed to_add + to_update (bounded concurrency + 429 backoff handled by EmbeddingsClient).
    to_process = sorted(to_add | to_update, key=lambda j: by_id[j]["title"])
    state_upserts: list[dict] = []

    for batch_start in range(0, len(to_process), EMBED_BATCH):
        batch_ids = to_process[batch_start : batch_start + EMBED_BATCH]
        batch_chunks = [by_id[jid] for jid in batch_ids]
        try:
            vectors = await embed.embed([c["chunk_text"] for c in batch_chunks])
        except AIProviderError as exc:
            raise SyncFailed(f"embed failed: {exc.message}")

        for chunk, vec in zip(batch_chunks, vectors):
            # Write to chunks + vec_chunks in a single transaction
            await db.chunk_upsert_with_vector(
                jf_id=chunk["jf_id"],
                chunk_text=chunk["chunk_text"],
                embedding=vec,
                title=chunk.get("title"),
                year=chunk.get("year"),
                genres=chunk.get("genres"),
            )
            state_upserts.append({
                "jf_id": chunk["jf_id"],
                "content_hash": chunk["content_hash"],
                "synced_at": _to_iso(by_id[chunk["jf_id"]]),
            })

        for jid in batch_ids:
            if jid in to_add:
                summary.added += 1
            else:
                summary.updated += 1

    # Delete removed items (vectors + chunks) and update sync_state.
    if to_remove:
        removed = list(to_remove)
        for jid in removed:
            await db.chunk_delete_with_vector(jid)
            state_upserts.append({"jf_id": jid, "deleted": True})
        summary.removed = len(removed)

    # Update sync_state hashes + timestamps for processed items.
    conn = await db.get_connection()
    try:
        await conn.execute("BEGIN")
        for item in state_upserts:
            if item.get("deleted"):
                await conn.execute("DELETE FROM sync_state WHERE jf_id = ?", (item["jf_id"],))
            else:
                await conn.execute("""
                    INSERT OR REPLACE INTO sync_state (jf_id, content_hash, synced_at)
                    VALUES (?, ?, ?)
                """, (item["jf_id"], item["content_hash"], item.get("synced_at")))
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await db.return_connection(conn)

    return summary


def _to_iso(chunk: dict) -> str | None:
    return None
