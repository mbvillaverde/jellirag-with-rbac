// Client-side auth + API helpers. Token stored in localStorage and sent via
// Authorization header (the SSE chat path uses fetch + ReadableStream, so it can
// set headers — unlike native EventSource). The backend also accepts an
// HttpOnly cookie fallback.

const TOKEN_KEY = 'jr_token'
const ROLE_KEY = 'jr_role'
const EMAIL_KEY = 'jr_email'

// API base is injected at build time from PUBLIC_API_BASE (Astro). Empty string
// means "same origin / relative" (works when frontend + API share a host).
export const API_BASE = import.meta.env.PUBLIC_API_BASE ?? ''

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getRole(): string | null {
  return localStorage.getItem(ROLE_KEY)
}

export function setSession(token: string, role: string, email: string) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(ROLE_KEY, role)
  localStorage.setItem(EMAIL_KEY, email)
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(ROLE_KEY)
  localStorage.removeItem(EMAIL_KEY)
}

export function authHeaders(): Record<string, string> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

// Redirect unauthenticated users to /login.
export function requireAuth(redirect = '/login'): boolean {
  if (!getToken()) {
    window.location.href = redirect
    return false
  }
  return true
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(options.headers || {}) },
  })
  if (resp.status === 401) {
    clearSession()
    window.location.href = '/login'
    throw new Error('unauthorized')
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new ApiError(resp.status, text || resp.statusText)
  }
  return resp.json() as Promise<T>
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}
