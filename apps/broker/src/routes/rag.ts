import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError, assertJfId, assertTopK, assertFilterBytes } from '../limits'
import { embedOne, embedBatch, LLM_MODEL } from '../ai'
import { chunk, rowsPerStatement, placeholders } from '../db'
import { readOwnerScopedHistoryWindow } from '../history'
import { readJson, type AppContext } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

interface ChunkRow {
  jf_id: string
  chunk_text: string
  title?: string
  year?: number
  genres?: string
}

// Fetch chunk text for a set of jf_ids, transparently chunking the IN-clause
// to <=100 bound parameters per D1 statement.
async function fetchChunks(c: AppContext, jfIds: string[]): Promise<ChunkRow[]> {
  if (jfIds.length === 0) return []
  const per = rowsPerStatement(1) // 1 param per row in the IN-clause
  const rows: ChunkRow[] = []
  for (const batch of chunk(jfIds, per)) {
    const { results } = await c.env.DB.prepare(
      `SELECT jf_id, chunk_text, title, year, genres FROM chunks WHERE jf_id IN ${placeholders(batch.length)}`,
    )
      .bind(...batch)
      .all()
    for (const r of results) {
      const row = r as Record<string, unknown>
      rows.push({
        jf_id: String(row.jf_id),
        chunk_text: String(row.chunk_text ?? ''),
        title: row.title as string | undefined,
        year: row.year as number | undefined,
        genres: row.genres as string | undefined,
      })
    }
  }
  return rows
}

interface RagRequest {
  session_id: string
  message: string
  top_k: number
  history_max_tokens: number
  owner_email: string
}

// POST /prepare-rag — HOT PATH. Fused: embed + Vectorize query + D1 chunk
// fetch + owner-scoped history-window read in one edge round-trip. Returns
// raw pieces; makes no budget/policy decisions.
app.post('/prepare-rag', async (c) => {
  const body = await readJson<RagRequest>(c)
  if (!body.session_id) throw new ValidationError('session_id required')
  if (!body.message || typeof body.message !== 'string')
    throw new ValidationError('message required')
  if (!body.owner_email) throw new ValidationError('owner_email required')

  const topK = assertTopK(body.top_k ?? 8)
  const historyMax = Number(body.history_max_tokens ?? 1024)

  const vector = await embedOne(c.env.AI, body.message)
  const queryResult = await c.env.INDEX.query(vector, {
    topK,
    returnMetadata: 'all',
  })

  const matches = (queryResult.matches ?? []).map((m) => ({
    jf_id: String(m.id),
    score: m.score,
    metadata: m.metadata,
  }))

  const jfIds = matches.map((m) => assertJfId(m.jf_id))
  const chunks = await fetchChunks(c, jfIds)

  const history = await readOwnerScopedHistoryWindow(
    c.env.DB,
    body.session_id,
    body.owner_email,
    historyMax,
  )

  return c.json({ matches, chunks, history })
})

// POST /search — fuse embed + Vectorize query.
app.post('/search', async (c) => {
  const body = await readJson<{ text: string; topK?: number; filter?: unknown }>(c)
  if (!body.text) throw new ValidationError('text required')
  const topK = assertTopK(body.topK ?? 8)
  const filter = assertFilterBytes(body.filter)

  const vector = await embedOne(c.env.AI, body.text)
  const queryResult = await c.env.INDEX.query(vector, {
    topK,
    returnMetadata: 'all',
    ...(filter ? { filter: filter as VectorizeVectorMetadataFilter } : {}),
  })

  const matches = (queryResult.matches ?? []).map((m) => ({
    jf_id: String(m.id),
    score: m.score,
    metadata: m.metadata,
  }))
  return c.json({ matches })
})

// POST /embed — delegate to the AI binding.
app.post('/embed', async (c) => {
  const body = await readJson<{ texts: string[] }>(c)
  if (!Array.isArray(body.texts) || body.texts.length === 0)
    throw new ValidationError('texts[] required')
  if (body.texts.length > 50) throw new ValidationError('too many texts in one batch')
  const vectors = await embedBatch(c.env.AI, body.texts)
  return c.json({ vectors })
})

// POST /chunks — D1 read by jf_id list (chunked to <=100 params/statement).
app.post('/chunks', async (c) => {
  const body = await readJson<{ jf_ids: string[] }>(c)
  if (!Array.isArray(body.jf_ids)) throw new ValidationError('jf_ids[] required')
  const ids = body.jf_ids.map((id) => assertJfId(id))
  const chunks = await fetchChunks(c, ids)
  return c.json({ chunks })
})

// POST /llm-stream — AI binding llama with stream:true; relay as SSE.
// FastAPI sets max_tokens explicitly (RESPONSE_HEADROOM); we forward it.
app.post('/llm-stream', async (c) => {
  const body = await readJson<{ messages: unknown[]; max_tokens?: number }>(c)
  if (!Array.isArray(body.messages) || body.messages.length === 0)
    throw new ValidationError('messages[] required')
  const maxTokens = Number(body.max_tokens)
  if (!Number.isInteger(maxTokens) || maxTokens <= 0)
    throw new ValidationError('max_tokens must be a positive integer')

  const stream = (await c.env.AI.run(LLM_MODEL, {
    messages: body.messages,
    stream: true,
    max_tokens: maxTokens,
  })) as unknown as ReadableStream

  return new Response(stream, {
    headers: {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache',
      connection: 'keep-alive',
    },
  })
})

export default app
