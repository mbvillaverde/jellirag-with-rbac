import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError } from '../limits'
import { readJson } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

// POST /sessions/prune — delete sessions with last_active_at < older_than,
// cascading to their messages (D1 enforces the FK cascade automatically).
// The cutoff is FastAPI-supplied; the broker makes no TTL policy decision.
// Returns counts of what was removed.
app.post('/sessions/prune', async (c) => {
  const body = await readJson<{ older_than: string }>(c)
  if (!body.older_than || typeof body.older_than !== 'string')
    throw new ValidationError('older_than required (ISO-8601)')

  const countSessions = c.env.DB.prepare(
    'SELECT COUNT(*) AS n FROM sessions WHERE last_active_at < ?',
  ).bind(body.older_than)
  const countMessages = c.env.DB.prepare(
    'SELECT COUNT(*) AS n FROM messages WHERE session_id IN ' +
      '(SELECT session_id FROM sessions WHERE last_active_at < ?)',
  ).bind(body.older_than)
  const deleteSessions = c.env.DB.prepare(
    'DELETE FROM sessions WHERE last_active_at < ?',
  ).bind(body.older_than)

  const results = await c.env.DB.batch([countSessions, countMessages, deleteSessions])

  const deleted_sessions = Number((results[0].results[0] as Record<string, unknown>)?.n ?? 0)
  const deleted_messages = Number((results[1].results[0] as Record<string, unknown>)?.n ?? 0)
  return c.json({ deleted_sessions, deleted_messages })
})

export default app
