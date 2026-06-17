import { Hono } from 'hono'
import type { Bindings } from './env'
import { requireBrokerSecret } from './security'
import { ValidationError } from './limits'
import rag from './routes/rag'
import history from './routes/history'
import ingest from './routes/ingest'
import sync from './routes/sync'
import sessions from './routes/sessions'
import users from './routes/users'

const app = new Hono<{ Bindings: Bindings }>()

// Liveness probe (unauthenticated) — distinguishes "broker up" from "auth ok".
app.get('/', (c) => c.json({ ok: true, service: 'jellirag-broker' }))

// Every domain operation requires a valid X-Broker-Secret (task 3.1).
app.use('*', requireBrokerSecret)

// Route groups (each delegates to bindings; the broker makes no policy).
app.route('/', rag)
app.route('/', history)
app.route('/', ingest)
app.route('/', sync)
app.route('/', sessions)
app.route('/', users)

// Validation errors -> 400; everything else -> 500 with a non-leaky message.
app.onError((err, c) => {
  if (err instanceof ValidationError) {
    return c.json({ error: err.message }, 400)
  }
  console.error('broker error:', err)
  return c.json({ error: 'internal error' }, 500)
})

export default app
