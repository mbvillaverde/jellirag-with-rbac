// Verified Cloudflare platform limits (design Appendix). These are the hard
// ceilings the broker honors on every endpoint. See specs/broker.
export const TOP_K_MAX = 20 // Vectorize max returned results
export const JF_ID_MAX_BYTES = 64 // Vectorize max vector id length
export const FILTER_MAX_BYTES = 2048 // Vectorize filter object compact JSON ceiling
export const EMBED_MAX_CHARS = 2048 // ~512 tokens @ ~4 chars/token (bge-small-en input)
export const EMBED_BATCH_MAX = 50 // sensible per-call batch cap for /embed
export const D1_MAX_PARAMS = 100 // D1 max bound parameters per statement
export const VECTORIZE_UPSERT_MAX = 1000 // Vectorize max vectors per upsert

export class ValidationError extends Error {}

const encoder = new TextEncoder()

export function byteLength(s: string): number {
  return encoder.encode(s).length
}

export function assertJfId(id: unknown, field = 'jf_id'): string {
  if (typeof id !== 'string' || id.length === 0) {
    throw new ValidationError(`${field} must be a non-empty string`)
  }
  if (byteLength(id) > JF_ID_MAX_BYTES) {
    throw new ValidationError(`${field} exceeds ${JF_ID_MAX_BYTES} bytes`)
  }
  return id
}

export function assertTopK(n: unknown): number {
  const k = Number(n)
  if (!Number.isInteger(k) || k < 1 || k > TOP_K_MAX) {
    throw new ValidationError(`top_k must be an integer in 1..${TOP_K_MAX}`)
  }
  return k
}

export function assertFilterBytes(filter: unknown): Record<string, unknown> | undefined {
  if (filter === undefined || filter === null) return undefined
  if (typeof filter !== 'object') {
    throw new ValidationError('filter must be an object')
  }
  // Compact JSON length is the Vectorize constraint.
  const compact = JSON.stringify(filter)
  if (compact.length >= FILTER_MAX_BYTES) {
    throw new ValidationError(`filter compact JSON must be < ${FILTER_MAX_BYTES} bytes`)
  }
  return filter as Record<string, unknown>
}

export function assertEmbedText(t: unknown): string {
  if (typeof t !== 'string' || t.length === 0) {
    throw new ValidationError('text must be a non-empty string')
  }
  // ~4 chars/token; bge-small-en input is ~512 tokens => ~2KB.
  if (t.length > EMBED_MAX_CHARS) {
    throw new ValidationError(`embedding input exceeds ~512 tokens (${EMBED_MAX_CHARS} chars)`)
  }
  return t
}
