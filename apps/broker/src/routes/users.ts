import { Hono } from 'hono'
import type { Bindings } from '../env'
import { ValidationError } from '../limits'
import { readJson } from '../lib'

const app = new Hono<{ Bindings: Bindings }>()

const VALID_ROLES = new Set(['admin', 'member'])

interface UserRow {
  email: string
  role: string
  pw_hash: string
  created_at: string
}

function assertRole(role: unknown): 'admin' | 'member' {
  if (!VALID_ROLES.has(String(role)))
    throw new ValidationError('role must be admin|member')
  return String(role) as 'admin' | 'member'
}

// POST /users/lookup — return the row (including the opaque pw_hash); perform
// NO password verification. FastAPI verifies locally.
app.post('/users/lookup', async (c) => {
  const body = await readJson<{ email: string }>(c)
  if (!body.email) throw new ValidationError('email required')
  const row = await c.env.DB.prepare('SELECT email, role, pw_hash FROM users WHERE email = ?')
    .bind(body.email)
    .first<UserRow>()
  if (!row) return c.json({ user: null })
  return c.json({ user: { email: row.email, role: row.role, pw_hash: row.pw_hash } })
})

// POST /users/create — insert a row. pw_hash is an opaque FastAPI-produced
// argon2id blob; the broker stores it verbatim.
app.post('/users/create', async (c) => {
  const body = await readJson<{ email: string; role: string; pw_hash: string }>(c)
  if (!body.email) throw new ValidationError('email required')
  if (typeof body.pw_hash !== 'string') throw new ValidationError('pw_hash required')
  const role = assertRole(body.role)
  const now = new Date().toISOString()
  try {
    await c.env.DB.prepare(
      'INSERT INTO users(email, role, pw_hash, created_at) VALUES(?, ?, ?, ?)',
    )
      .bind(body.email, role, body.pw_hash, now)
      .run()
  } catch (err) {
    if (String(err).includes('UNIQUE')) return c.json({ error: 'user exists' }, 409)
    throw err
  }
  return c.json({ ok: true })
})

// POST /users/list — never return pw_hash.
app.post('/users/list', async (c) => {
  const { results } = await c.env.DB.prepare(
    'SELECT email, role, created_at FROM users ORDER BY created_at',
  ).all<{ email: string; role: string; created_at: string }>()
  return c.json({ users: results })
})

// POST /users/update — partial update (role change and/or password reset).
app.post('/users/update', async (c) => {
  const body = await readJson<{
    email: string
    role?: string
    pw_hash?: string
  }>(c)
  if (!body.email) throw new ValidationError('email required')

  const sets: string[] = []
  const binds: unknown[] = []
  if (body.role !== undefined) {
    binds.push(assertRole(body.role))
    sets.push('role = ?')
  }
  if (body.pw_hash !== undefined) {
    binds.push(body.pw_hash)
    sets.push('pw_hash = ?')
  }
  if (sets.length === 0) throw new ValidationError('nothing to update')
  binds.push(body.email)

  await c.env.DB.prepare(`UPDATE users SET ${sets.join(', ')} WHERE email = ?`)
    .bind(...binds)
    .run()
  return c.json({ ok: true })
})

// POST /users/delete — cascade-delete the user's sessions and messages
// (enforced automatically via users->sessions->messages FK CASCADE).
// Returns counts of everything removed.
app.post('/users/delete', async (c) => {
  const body = await readJson<{ email: string }>(c)
  if (!body.email) throw new ValidationError('email required')

  const countSessions = c.env.DB.prepare(
    'SELECT COUNT(*) AS n FROM sessions WHERE owner_email = ?',
  ).bind(body.email)
  const countMessages = c.env.DB.prepare(
    'SELECT COUNT(*) AS n FROM messages WHERE session_id IN ' +
      '(SELECT session_id FROM sessions WHERE owner_email = ?)',
  ).bind(body.email)
  const deleteUser = c.env.DB.prepare('DELETE FROM users WHERE email = ?').bind(body.email)

  const results = await c.env.DB.batch([countSessions, countMessages, deleteUser])
  const deleted_sessions = Number((results[0].results[0] as Record<string, unknown>)?.n ?? 0)
  const deleted_messages = Number((results[1].results[0] as Record<string, unknown>)?.n ?? 0)
  return c.json({ deleted_users: results[2].meta.changes ?? 0, deleted_sessions, deleted_messages })
})

export default app
