<template>
  <div class="chat">
    <div class="chat__head">
      <h1>JellieRAG</h1>
      <div class="chat__actions">
        <button class="btn btn--ghost" @click="newChat" :disabled="streaming">New chat</button>
        <button class="btn btn--ghost" v-if="role === 'admin'" @click="goAdmin">Admin</button>
        <button class="btn btn--ghost" @click="logout">Log out</button>
      </div>
    </div>

    <div class="chat__messages" ref="scrollEl">
      <div v-for="(m, i) in messages" :key="i" :class="['msg', `msg--${m.role}`]">
        <div class="msg__who">{{ m.role === 'user' ? 'You' : 'Jellie' }}</div>
        <div class="msg__body" v-html="renderMarkdown(m.content)"></div>
        <div class="msg__sources" v-if="m.sources && m.sources.length">
          <span
            v-for="jf in m.sources"
            :key="jf"
            class="chip chip--rich"
            :title="`Requires Tailscale — opens at ${deeplinkBase}`"
          >
            <a class="chip__main" :href="chipLink(jf)" target="_blank" rel="noopener noreferrer">
              <span class="chip__image">
                <img
                  v-if="getMetadata(jf).image_url && !imgFailed[jf]"
                  :src="imgSrc(jf)"
                  :alt="getMetadata(jf).title || jf"
                  loading="lazy"
                  @error="imgFailed[jf] = true"
                />
                <span v-else class="chip__icon" aria-hidden="true">🎬</span>
              </span>
              <span class="chip__meta" v-if="hasMetadata(jf)">
                <span class="chip__title">{{ getMetadata(jf).title || jf }}</span>
                <span class="chip__sub">
                  <span class="chip__year" v-if="getMetadata(jf).year">{{ getMetadata(jf).year }}</span>
                  <span class="chip__genres" v-if="getMetadata(jf).genres">{{ getMetadata(jf).genres }}</span>
                </span>
              </span>
              <span class="chip__meta" v-else>
                <span class="chip__title">{{ jf }}</span>
              </span>
            </a>
            <span class="chip__hint">needs Tailscale →</span>
          </span>
        </div>
      </div>
      <div class="msg msg--assistant" v-if="streaming && streamingAssistant">
        <div class="msg__who">Jellie</div>
        <div class="msg__body" v-html="renderMarkdown(streamingAssistant)"></div>
      </div>
    </div>

    <form class="chat__input" @submit.prevent="send">
      <textarea
        v-model="input"
        :disabled="streaming"
        placeholder="Ask about a movie or series in your library…"
        @keydown.enter.exact.prevent="send"
      ></textarea>
      <button class="btn btn--primary" type="submit" :disabled="streaming || !input.trim()">
        {{ streaming ? 'Streaming…' : 'Send' }}
      </button>
    </form>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, onMounted } from 'vue'
import { API_BASE, getToken, getRole, clearSession, authHeaders, requireAuth } from '../lib/auth'

const props = defineProps<{ deeplinkBase: string }>()
const deeplinkBase = props.deeplinkBase

interface SourceMeta {
  title?: string
  year?: number
  genres?: string
  image_url?: string
}

interface Msg {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
}

const messages = ref<Msg[]>([])
const input = ref('')
const streaming = ref(false)
const scrollEl = ref<HTMLElement | null>(null)
const streamingAssistant = ref('')
const sessionId = ref(crypto.randomUUID())
const role = ref<string | null>(null)

// Rich-chip metadata keyed by jf_id. Metadata is immutable per item, so a single
// accumulating map is shared across all messages. `imgFailed` tracks per-id
// graceful-degradation state so a broken thumbnail falls back to a film icon.
const sourceMetadata = ref<Record<string, SourceMeta>>({})
const imgFailed = ref<Record<string, boolean>>({})

onMounted(() => {
  if (!requireAuth()) return
  role.value = getRole()
})

function newChat() {
  sessionId.value = crypto.randomUUID()
  messages.value = []
  streamingAssistant.value = ''
}

function goAdmin() {
  window.location.href = '/admin'
}

function logout() {
  clearSession()
  window.location.href = '/login'
}

function chipLink(jfId: string): string {
  return `${deeplinkBase.replace(/\/$/, '')}/web/index.html#/details?id=${jfId}`
}

// Metadata for a cited item, or an empty object (drives fallback rendering).
function getMetadata(jfId: string): SourceMeta {
  return sourceMetadata.value[jfId] ?? {}
}

// True if we have any of title/year/genres to render as text.
function hasMetadata(jfId: string): boolean {
  const m = getMetadata(jfId)
  return Boolean(m.title || m.year || m.genres)
}

// Absolute image URL with auth token. `<img>` cannot set Authorization headers,
// so the token is passed via the query string (the backend accepts ?token=).
function imgSrc(jfId: string): string {
  const meta = getMetadata(jfId)
  const base = meta.image_url ?? `/api/jellyfin/image?id=${jfId}`
  const url = `${API_BASE}${base}`
  const token = getToken()
  return token ? `${url}&token=${encodeURIComponent(token)}` : url
}

// Escape HTML, then re-introduce safe anchor tags for markdown links and bare
// http(s) URLs. Deep links fail closed off-Tailnet (NXDOMAIN).
function renderMarkdown(text: string): string {
  if (!text) return ''
  const escaped = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  return escaped
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])((https?:\/\/[^\s<)]+))/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>')
    .replace(/\n/g, '<br>')
}

async function scrollToBottom() {
  await nextTick()
  const el = scrollEl.value
  if (el) el.scrollTop = el.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  if (!getToken()) {
    window.location.href = '/login'
    return
  }

  messages.value.push({ role: 'user', content: text })
  input.value = ''
  streaming.value = true
  streamingAssistant.value = ''
  await scrollToBottom()

  try {
    const resp = await fetch(`${API_BASE}/api/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...authHeaders(),
      },
      body: JSON.stringify({ session_id: sessionId.value, message: text }),
    })
    if (resp.status === 401) {
      clearSession()
      window.location.href = '/login'
      return
    }
    if (!resp.ok || !resp.body) {
      throw new Error(`chat failed (${resp.status})`)
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let sources: string[] | undefined
    let assembled = ''

    // SSE buffer: reassemble across read() boundaries before emitting.
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const event = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        for (const line of event.split('\n')) {
          if (!line.startsWith('data:')) continue
          const payload = line.slice(5).trim()
          if (payload === '[DONE]') continue
          let data: any
          try {
            data = JSON.parse(payload)
          } catch {
            continue
          }
          if (Array.isArray(data.sources)) {
            sources = data.sources
            // Merge per-source metadata (title/year/genres/image_url) for rich
            // chips. Metadata is immutable per jf_id, so a shallow merge keeps
            // earlier values intact while filling in newly-cited items.
            if (data.source_metadata && typeof data.source_metadata === 'object') {
              sourceMetadata.value = { ...sourceMetadata.value, ...data.source_metadata }
            }
          } else if (typeof data.response === 'string') {
            assembled += data.response
            streamingAssistant.value = assembled
            await scrollToBottom()
          }
        }
      }
    }

    messages.value.push({ role: 'assistant', content: assembled, sources })
  } catch (err) {
    messages.value.push({
      role: 'assistant',
      content: 'Sorry — something went wrong reaching the assistant.',
    })
  } finally {
    streamingAssistant.value = ''
    streaming.value = false
    await scrollToBottom()
  }
}
</script>

<style scoped>
.chat { display: flex; flex-direction: column; height: 100vh; max-width: 820px; margin: 0 auto; padding: 1rem; box-sizing: border-box; font-family: system-ui, sans-serif; }
.chat__head { display: flex; align-items: center; justify-content: space-between; }
.chat__head h1 { font-size: 1.25rem; margin: 0; }
.chat__actions { display: flex; gap: .5rem; }
.chat__messages { flex: 1; overflow-y: auto; padding: 1rem 0; display: flex; flex-direction: column; gap: .75rem; }
.msg { max-width: 80%; padding: .6rem .85rem; border-radius: 12px; line-height: 1.4; }
.msg--user { background: #2563eb; color: #fff; align-self: flex-end; }
.msg--assistant { background: #f1f5f9; color: #0f172a; align-self: flex-start; }
.msg__who { font-size: .7rem; opacity: .7; margin-bottom: .2rem; }
.msg__body :deep(a) { color: inherit; text-decoration: underline; }
.msg__sources { margin-top: .5rem; display: flex; flex-wrap: wrap; gap: .35rem; }
.chip { font-size: .7rem; background: rgba(255,255,255,.2); border-radius: 999px; padding: .1rem .5rem; display: inline-flex; align-items: center; gap: .3rem; }
.msg--assistant .chip { background: #e2e8f0; color: #334155; }
.chip__hint { opacity: .6; font-size: .6rem; }

/* Rich chips: thumbnail | metadata, horizontal media-card layout. */
.chip--rich {
  align-items: stretch;
  border-radius: 8px;
  padding: .25rem;
  max-width: 100%;
}
.chip__main {
  display: inline-flex;
  align-items: center;
  gap: .4rem;
  text-decoration: none;
  color: inherit;
  min-width: 0;
}
.chip__image {
  flex: 0 0 auto;
  width: 28px;
  height: 42px;
  border-radius: 4px;
  overflow: hidden;
  background: rgba(0,0,0,.12);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.chip__image img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.chip__icon { font-size: .9rem; line-height: 1; }
.chip__meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: 180px;
  line-height: 1.2;
}
.chip__title {
  font-weight: 600;
  font-size: .72rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chip__sub {
  display: flex;
  flex-wrap: wrap;
  gap: .25rem;
  font-size: .6rem;
  opacity: .75;
  margin-top: .05rem;
}
.chip__year { font-variant-numeric: tabular-nums; }
.chip__genres {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
/* Assistant (light) chip color overrides. */
.msg--assistant .chip__image { background: #cbd5e1; }
.chat__input { display: flex; gap: .5rem; padding: .5rem 0; }
.chat__input textarea { flex: 1; resize: none; height: 3rem; padding: .6rem; border-radius: 10px; border: 1px solid #cbd5e1; font: inherit; }
.btn { border: 1px solid transparent; border-radius: 10px; padding: .5rem .85rem; cursor: pointer; font: inherit; background: #fff; color: #0f172a; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn--primary { background: #2563eb; color: #fff; }
.btn--ghost { background: transparent; border-color: #cbd5e1; }
</style>
