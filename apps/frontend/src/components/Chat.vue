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
            class="chip"
            :title="`Requires Tailscale — opens at ${deeplinkBase}`"
          >
            <a :href="chipLink(jf)" target="_blank" rel="noopener noreferrer">{{ jf }}</a>
            <span class="chip__hint">needs Tailscale</span>
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
.chat__input { display: flex; gap: .5rem; padding: .5rem 0; }
.chat__input textarea { flex: 1; resize: none; height: 3rem; padding: .6rem; border-radius: 10px; border: 1px solid #cbd5e1; font: inherit; }
.btn { border: 1px solid transparent; border-radius: 10px; padding: .5rem .85rem; cursor: pointer; font: inherit; background: #fff; color: #0f172a; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn--primary { background: #2563eb; color: #fff; }
.btn--ghost { background: transparent; border-color: #cbd5e1; }
</style>
