<template>
  <form class="login" @submit.prevent="submit">
    <h1>JellieRAG</h1>
    <p class="login__sub">Sign in to chat with your media library.</p>

    <label>
      Email
      <input type="email" v-model="email" required autocomplete="username" :disabled="loading" />
    </label>
    <label>
      Password
      <input type="password" v-model="password" required autocomplete="current-password" :disabled="loading" />
    </label>

    <p class="login__error" v-if="error">{{ error }}</p>

    <button class="btn btn--primary" type="submit" :disabled="loading">
      {{ loading ? 'Signing in…' : 'Sign in' }}
    </button>
  </form>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { API_BASE, setSession } from '../lib/auth'

const email = ref('')
const password = ref('')
const loading = ref(false)
const error = ref('')

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const resp = await fetch(`${API_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: email.value.trim().toLowerCase(), password: password.value }),
    })
    if (resp.status === 401) {
      error.value = 'Invalid email or password.'
      return
    }
    if (resp.status === 429) {
      error.value = 'Too many attempts. Please try again shortly.'
      return
    }
    if (!resp.ok) {
      error.value = 'Sign-in failed. Please try again.'
      return
    }
    const data = await resp.json()
    setSession(data.token, data.role, data.email)
    window.location.href = '/'
  } catch {
    error.value = 'Could not reach the server.'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login { max-width: 360px; margin: 6vh auto; display: flex; flex-direction: column; gap: .75rem; font-family: system-ui, sans-serif; }
.login h1 { margin: 0; text-align: center; }
.login__sub { margin: 0 0 .5rem; text-align: center; color: #64748b; font-size: .9rem; }
label { display: flex; flex-direction: column; gap: .25rem; font-size: .85rem; color: #334155; }
input { padding: .6rem; border-radius: 10px; border: 1px solid #cbd5e1; font: inherit; }
.btn { border: 1px solid transparent; border-radius: 10px; padding: .6rem .85rem; cursor: pointer; font: inherit; }
.btn--primary { background: #2563eb; color: #fff; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.login__error { color: #dc2626; font-size: .85rem; margin: 0; }
</style>
