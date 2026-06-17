import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError } from '../limits'
import { readOwnerScopedHistoryWindow } from '../history'
import { readJson } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

// POST /history/read — newest-preferred, token-bounded, owner-scoped window.
app.post('/history/read', async (c) => {
  const body = await readJson<{
    session_id: string
    owner_email: string
    max_tokens: number
  }>(c)
  if (!body.session_id) throw new ValidationError('session_id required')
  if (!body.owner_email) throw new ValidationError('owner_email required')
  const maxTokens = Number(body.max_tokens ?? 1024)
  if (!Number.isFinite(maxTokens) || maxTokens <= 0)
    throw new ValidationError('max_tokens must be positive')

  const history = await readOwnerScopedHistoryWindow(
    c.env.DB,
    body.session_id,
    body.owner_email,
    maxTokens,
  )
  return c.json({ history })
})

// POST /history/append — append a turn and atomically upsert the session row
// (stamping owner_email from the request body, which FastAPI has validated to
// match the caller's JWT) + bump last_active_at. Runs in a single D1 batch
// (implicit transaction). On an existing session, owner_email is preserved
// and only last_active_at advances.
app.post('/history/append', async (c) => {
  const body = await readJson<{
    session_id: string
    owner_email: string
    role: 'system' | 'user' | 'assistant'
    content: string
    token_count?: number
  }>(c)
  if (!body.session_id) throw new ValidationError('session_id required')
  if (!body.owner_email) throw new ValidationError('owner_email required')
  if (!['system', 'user', 'assistant'].includes(body.role))
    throw new ValidationError('role must be system|user|assistant')
  if (typeof body.content !== 'string' || body.content.length === 0)
    throw new ValidationError('content required')

  const now = new Date().toISOString()
  const tokenCount = Number(body.token_count ?? 0)

  const upsertSession = c.env.DB.prepare(
    'INSERT INTO sessions(session_id, owner_email, created_at, last_active_at) ' +
      'VALUES(?, ?, ?, ?) ' +
      'ON CONFLICT(session_id) DO UPDATE SET last_active_at = excluded.last_active_at',
  )
    .bind(body.session_id, body.owner_email, now, now)

  const insertMessage = c.env.DB.prepare(
    'INSERT INTO messages(session_id, seq, role, content, token_count, created_at) ' +
      'VALUES(?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?), ?, ?, ?, ?)',
  )
    .bind(body.session_id, body.session_id, body.role, body.content, tokenCount, now)

  await c.env.DB.batch([upsertSession, insertMessage])

  return c.json({ ok: true })
})

export default app
