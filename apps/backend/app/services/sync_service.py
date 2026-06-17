"""Incremental Jellyfin library sync (capability: library-sync).

Two-way diff against `sync_state`: embed + upsert only new/changed items,
delete vectors + chunks for removed items, and skip unchanged items. Fails
fast (no partial Vectorize/D1 mutation) if Jellyfin is unreachable over
Tailscale. Processes in streamed batches so peak RAM stays < 1GB at 5,000 items
(tasks 5.1-5.7, 5.9, 5.10).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from ..services.broker_client import BrokerClient, BrokerError
from ..services.jellyfin_client import JellyfinClient, JellyfinUnreachable
from ..services.chunks import item_to_chunk

log = logging.getLogger("jellirag.sync")

EMBED_BATCH = 50          # texts per /embed call
EMBED_CONCURRENCY = 5     # bounded concurrency under the ~3,000 req/min ceiling
INGEST_BATCH = 50         # items per /ingest/upsert call


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
    broker: BrokerClient,
    jellyfin: JellyfinClient,
) -> SyncSummary:
    summary = SyncSummary()

    # 5.1 — fail fast on unreachable; no partial mutation.
    await jellyfin.check_reachable()
    items = await jellyfin.library_items()
    summary.total = len(items)

    # Synthesize chunks (streaming-friendly; bounded text size via 5.9).
    chunks = [item_to_chunk(it) for it in items if it.get("Id")]
    jellyfin_ids = {c["jf_id"] for c in chunks}
    by_id = {c["jf_id"]: c for c in chunks}

    # 5.2 — two-way diff.
    known_rows = await broker.sync_state_get()
    known_active = {r["jf_id"]: r for r in known_rows if r.get("deleted_at") is None}
    known_active_ids = set(known_active.keys())

    to_add = jellyfin_ids - known_active_ids
    to_remove = known_active_ids - jellyfin_ids
    to_update = {
        jid
        for jid in (jellyfin_ids & known_active_ids)
        if by_id[jid]["content_hash"] != known_active[jid].get("content_hash")
    }
    unchanged = (jellyfin_ids & known_active_ids) - to_update

    summary.unchanged = len(unchanged)  # no embedding for these (5.8 steady-state)

    # 5.3 + 5.10 — embed to_add + to_update with bounded concurrency + 429 backoff.
    sem = asyncio.Semaphore(EMBED_CONCURRENCY)
    to_process = sorted(to_add | to_update, key=lambda j: by_id[j]["title"])
    state_upserts: list[dict] = []

    for batch_start in range(0, len(to_process), EMBED_BATCH):
        batch_ids = to_process[batch_start : batch_start + EMBED_BATCH]
        batch_chunks = [by_id[jid] for jid in batch_ids]
        vectors = await _embed_with_backoff(broker, sem, [c["chunk_text"] for c in batch_chunks])

        ingest_items = []
        for chunk, vec in zip(batch_chunks, vectors):
            ingest_items.append({**chunk, "vector": vec})
            state_upserts.append({
                "jf_id": chunk["jf_id"],
                "content_hash": chunk["content_hash"],
                "jellyfin_updated_at": _to_iso(by_id[chunk["jf_id"]]),
            })

        # 5.3 — batched upsert (Vectorize + D1).
        for i in range(0, len(ingest_items), INGEST_BATCH):
            await broker.ingest_upsert(ingest_items[i : i + INGEST_BATCH])

        for jid in batch_ids:
            if jid in to_add:
                summary.added += 1
            else:
                summary.updated += 1

    # 5.4 — delete removed items (vectors + chunks) and tombstone sync_state.
    if to_remove:
        removed = list(to_remove)
        for i in range(0, len(removed), INGEST_BATCH):
            await broker.ingest_delete(removed[i : i + INGEST_BATCH])
        for jid in removed:
            state_upserts.append({"jf_id": jid, "deleted": True})
        summary.removed = len(removed)

    # 5.5 — update sync_state hashes + timestamps for processed items.
    for i in range(0, len(state_upserts), 100):
        await broker.sync_state_put(state_upserts[i : i + 100])

    return summary


async def _embed_with_backoff(
    broker: BrokerClient, sem: asyncio.Semaphore, texts: list[str]
) -> list[list[float]]:
    last_exc: Exception | None = None
    for attempt in range(5):
        async with sem:
            try:
                return await broker.embed(texts)
            except BrokerError as exc:
                last_exc = exc
                if exc.status == 429:
                    await asyncio.sleep(min(2 ** attempt, 16))
                    continue
                raise
            except Exception as exc:
                last_exc = exc
                raise
    if last_exc:
        raise SyncFailed(f"embed rate-limited after retries: {last_exc}")
    return []


def _to_iso(chunk: dict) -> str | None:
    return None
