import type { D1Database } from '@cloudflare/workers-types'

export interface HistoryTurn {
  seq: number
  role: 'system' | 'user' | 'assistant'
  content: string
  token_count: number
  created_at: string
}

// ~4 chars/token heuristic (mirrors FastAPI's budget estimator).
function estimateTokens(content: string, stored: number): number {
  return stored > 0 ? stored : Math.ceil(content.length / 4)
}

// Read a token-bounded history window for (session_id, owner_email), newest
// turns preferred, returned in chronological order. The broker performs only
// a parameterized bounded read; FastAPI reconciles the final budget.
//
// Scope is enforced by a JOIN on sessions.owner_email, so a foreign session_id
// (owned by another user) yields zero rows — "new session" behavior.
export async function readOwnerScopedHistoryWindow(
  db: D1Database,
  sessionId: string,
  ownerEmail: string,
  maxTokens: number,
): Promise<HistoryTurn[]> {
  const { results } = await db
    .prepare(
      'SELECT m.seq AS seq, m.role AS role, m.content AS content, ' +
        'm.token_count AS token_count, m.created_at AS created_at ' +
        'FROM messages m JOIN sessions s ON s.session_id = m.session_id ' +
        'WHERE m.session_id = ? AND s.owner_email = ? ' +
        'ORDER BY m.seq DESC',
    )
    .bind(sessionId, ownerEmail)
    .all<HistoryTurn>()

  const window: HistoryTurn[] = []
  let used = 0
  for (const row of results) {
    const t = estimateTokens(row.content, row.token_count)
    if (used + t > maxTokens && window.length > 0) break
    window.push(row)
    used += t
    if (used >= maxTokens) break
  }
  window.reverse()
  return window
}
