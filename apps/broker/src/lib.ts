import type { Context } from 'hono'
import type { Bindings } from './env'
import { ValidationError } from './limits'

// Parse JSON body, raising a ValidationError (-> 400) on malformed input.
export async function readJson<T>(c: Context<{ Bindings: Bindings }>): Promise<T> {
  try {
    return (await c.req.json()) as T
  } catch {
    throw new ValidationError('invalid JSON body')
  }
}

export type AppContext = Context<{ Bindings: Bindings }>
