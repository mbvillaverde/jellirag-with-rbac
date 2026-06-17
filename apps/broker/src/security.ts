import type { Context } from 'hono'
import type { Bindings } from './env'

// Constant-time comparison of the shared broker secret. We hash both values
// with SHA-256 and compare fixed-length digests byte-by-byte, which avoids
// leaking the secret's length or early-exit position through timing.
export async function constantTimeEqual(a: string, b: string): Promise<boolean> {
  const enc = new TextEncoder()
  const [ha, hb] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b)),
  ])
  const da = new Uint8Array(ha)
  const db = new Uint8Array(hb)
  let diff = 0
  for (let i = 0; i < da.length; i++) diff |= da[i] ^ db[i]
  return diff === 0
}

// X-Broker-Secret auth middleware. Rejects with 401 on any mismatch and
// performs no Cloudflare operation.
export async function requireBrokerSecret(
  c: Context<{ Bindings: Bindings }>,
  next: () => Promise<void>,
): Promise<Response | void> {
  const provided = c.req.header('X-Broker-Secret')
  if (!provided) {
    return c.json({ error: 'unauthorized' }, 401)
  }
  const ok = await constantTimeEqual(provided, c.env.BROKER_SECRET)
  if (!ok) {
    return c.json({ error: 'unauthorized' }, 401)
  }
  await next()
}
