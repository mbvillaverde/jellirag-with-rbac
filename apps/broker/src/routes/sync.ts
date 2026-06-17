import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError, assertJfId } from '../limits'
import { chunk, rowsPerStatement, placeholders } from '../db'
import { readJson } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

interface SyncStateRow {
  jf_id: string
  content_hash: string | null
  last_synced_at: string | null
  deleted_at: string | null
  jellyfin_updated_at: string | null
}

// GET /sync/state — read non-deleted sync bookkeeping (the "known" set for the
// two-way diff). Returns all active rows; large libraries fit in one query.
app.get('/sync/state', async (c) => {
  const { results } = await c.env.DB.prepare(
    'SELECT jf_id, content_hash, last_synced_at, deleted_at, jellyfin_updated_at ' +
      'FROM sync_state WHERE deleted_at IS NULL',
  ).all<SyncStateRow>()
  return c.json({ items: results })
})

// PUT /sync/state — overwrite per-item sync bookkeeping (chunked <=100 params).
// A row with deleted:true stamps deleted_at (tombstone); the row is kept so the
// diff can avoid re-adding an item whose jf_id was retired.
app.put('/sync/state', async (c) => {
  const body = await readJson<{
    items: Array<{
      jf_id: string
      content_hash?: string | null
      jellyfin_updated_at?: string | null
      deleted?: boolean
    }>
  }>(c)
  if (!Array.isArray(body.items)) throw new ValidationError('items[] required')

  const now = new Date().toISOString()
  const COLS = 5 // params per row
  const per = rowsPerStatement(COLS)
  const oneRow = placeholders(COLS)

  type Norm = { jf_id: string; content_hash: string | null; last_synced_at: string; deleted_at: string | null; jellyfin_updated_at: string | null }
  const normalized: Norm[] = body.items.map((it) => {
    assertJfId(it.jf_id)
    return {
      jf_id: it.jf_id,
      content_hash: it.content_hash ?? null,
      last_synced_at: now,
      deleted_at: it.deleted ? now : null,
      jellyfin_updated_at: it.jellyfin_updated_at ?? null,
    }
  })

  const stmts = chunk(normalized, per).map((batch) =>
    c.env.DB.prepare(
      `INSERT INTO sync_state(jf_id, content_hash, last_synced_at, deleted_at, jellyfin_updated_at) ` +
        `VALUES ${batch.map(() => oneRow).join(',')} ` +
        'ON CONFLICT(jf_id) DO UPDATE SET content_hash=excluded.content_hash, ' +
        'last_synced_at=excluded.last_synced_at, deleted_at=excluded.deleted_at, ' +
        'jellyfin_updated_at=excluded.jellyfin_updated_at',
    ).bind(...batch.flatMap((it) => [it.jf_id, it.content_hash, it.last_synced_at, it.deleted_at, it.jellyfin_updated_at])),
  )

  if (stmts.length) await c.env.DB.batch(stmts)
  return c.json({ upserted: normalized.length })
})

export default app
