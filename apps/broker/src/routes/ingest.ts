import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError, assertJfId, assertEmbedText, VECTORIZE_UPSERT_MAX } from '../limits'
import { chunk, rowsPerStatement, placeholders } from '../db'
import { readJson } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

interface IngestItem {
  jf_id: string
  vector: number[]
  title: string
  year?: number
  genres?: string
  cast?: string
  overview?: string
  chunk_text: string
  content_hash: string
}

// chunks table columns written per row (count = bound params per row).
const CHUNK_COLS = 9

// POST /ingest/upsert — Vectorize upsert (slim metadata) + D1 chunks write.
// Vectorize payloads are split into <=1000-vector upserts; D1 statements use
// <=100 bound parameters each.
app.post('/ingest/upsert', async (c) => {
  const body = await readJson<{ items: IngestItem[] }>(c)
  if (!Array.isArray(body.items)) throw new ValidationError('items[] required')

  const now = new Date().toISOString()

  // Validate + normalize up-front (fail fast, no partial mutation).
  const normalized = body.items.map((it) => {
    if (!it) throw new ValidationError('invalid item')
    assertJfId(it.jf_id)
    if (!Array.isArray(it.vector)) throw new ValidationError('item.vector required')
    if (typeof it.chunk_text !== 'string')
      throw new ValidationError('item.chunk_text required')
    assertEmbedText(it.chunk_text) // chunk_text must fit the embedding model input
    if (typeof it.title !== 'string') throw new ValidationError('item.title required')
    if (typeof it.content_hash !== 'string')
      throw new ValidationError('item.content_hash required')
    return {
      jf_id: it.jf_id,
      vector: it.vector,
      title: it.title,
      year: it.year ?? null,
      genres: it.genres ?? null,
      cast: it.cast ?? null,
      overview: it.overview ?? null,
      chunk_text: it.chunk_text,
      content_hash: it.content_hash,
    }
  })

  // --- Vectorize: slim metadata only, chunked to <=1000 vectors per upsert. ---
  for (const batch of chunk(normalized, VECTORIZE_UPSERT_MAX)) {
    await c.env.INDEX.upsert(
      batch.map((it) => ({
        id: it.jf_id,
        values: it.vector,
        metadata: {
          jf_id: it.jf_id,
          title: it.title,
          ...(it.year !== null ? { year: it.year } : {}),
          ...(it.genres !== null ? { genre: it.genres } : {}),
        },
      })),
    )
  }

  // --- D1 chunks: chunked to <=100 bound params per statement. ---
  const per = rowsPerStatement(CHUNK_COLS)
  const colList = '(jf_id, title, year, genres, cast, overview, chunk_text, content_hash, updated_at)'
  const oneRow = placeholders(CHUNK_COLS)
  const batches: D1PreparedStatement[] = []
  for (const batch of chunk(normalized, per)) {
    const groups = batch.map(() => oneRow).join(',')
    batches.push(
      c.env.DB.prepare(
        `INSERT INTO chunks${colList} VALUES ${groups} ` +
          'ON CONFLICT(jf_id) DO UPDATE SET title=excluded.title, year=excluded.year, ' +
          'genres=excluded.genres, cast=excluded.cast, overview=excluded.overview, ' +
          'chunk_text=excluded.chunk_text, content_hash=excluded.content_hash, updated_at=excluded.updated_at',
      ).bind(...batch.flatMap((it) => [it.jf_id, it.title, it.year, it.genres, it.cast, it.overview, it.chunk_text, it.content_hash, now])),
    )
  }
  if (batches.length) await c.env.DB.batch(batches)

  return c.json({ upserted: normalized.length })
})

// POST /ingest/delete — Vectorize delete + D1 chunks delete (batched).
app.post('/ingest/delete', async (c) => {
  const body = await readJson<{ jf_ids: string[] }>(c)
  if (!Array.isArray(body.jf_ids)) throw new ValidationError('jf_ids[] required')
  const ids = body.jf_ids.map((id) => assertJfId(id))

  for (const batch of chunk(ids, VECTORIZE_UPSERT_MAX)) {
    await c.env.INDEX.deleteByIds(batch)
  }

  const per = rowsPerStatement(1)
  const stmts = chunk(ids, per).map((batch) =>
    c.env.DB.prepare(`DELETE FROM chunks WHERE jf_id IN ${placeholders(batch.length)}`).bind(...batch),
  )
  if (stmts.length) await c.env.DB.batch(stmts)

  return c.json({ deleted: ids.length })
})

export default app
