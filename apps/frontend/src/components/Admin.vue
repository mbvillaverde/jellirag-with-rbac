<template>
  <div class="admin">
    <div class="admin__head">
      <h1>Account management</h1>
      <div class="admin__actions">
        <button class="btn btn--ghost" @click="goChat">Back to chat</button>
        <button class="btn btn--ghost" @click="logout">Log out</button>
      </div>
    </div>

    <p class="admin__error" v-if="error">{{ error }}</p>

    <form class="admin__form" @submit.prevent="create">
      <input v-model="newEmail" type="email" placeholder="email" required />
      <input v-model="newPassword" type="password" placeholder="password" required />
      <select v-model="newRole">
        <option value="member">member</option>
        <option value="admin">admin</option>
      </select>
      <button class="btn btn--primary" type="submit">Add user</button>
    </form>

    <table class="admin__table">
      <thead>
        <tr><th>Email</th><th>Role</th><th>Created</th><th></th></tr>
      </thead>
      <tbody>
        <tr v-for="u in users" :key="u.email">
          <td>{{ u.email }}</td>
          <td>{{ u.role }}</td>
          <td>{{ shortDate(u.created_at) }}</td>
          <td class="admin__rowactions">
            <button class="btn btn--ghost" @click="reset(u.email)">Reset password</button>
            <button class="btn btn--ghost" @click="remove(u.email)">Delete</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { api, clearSession, ApiError } from '../lib/auth'

interface User { email: string; role: string; created_at: string }

const users = ref<User[]>([])
const error = ref('')
const newEmail = ref('')
const newPassword = ref('')
const newRole = ref('member')

onMounted(load)

async function load() {
  error.value = ''
  try {
    const data = await api<{ users: User[] }>('/api/admin/users')
    users.value = data.users
  } catch (e) {
    if (e instanceof ApiError && e.status === 403) {
      error.value = 'Admins only.'
    } else {
      error.value = 'Could not load users.'
    }
  }
}

async function create() {
  error.value = ''
  try {
    await api('/api/admin/users', {
      method: 'POST',
      body: JSON.stringify({ email: newEmail.value.trim().toLowerCase(), password: newPassword.value, role: newRole.value }),
    })
    newEmail.value = ''
    newPassword.value = ''
    await load()
  } catch (e) {
    error.value = e instanceof ApiError && e.status === 409 ? 'User already exists.' : 'Could not create user.'
  }
}

async function reset(email: string) {
  const password = window.prompt(`New password for ${email}`)
  if (!password) return
  try {
    await api(`/api/admin/users/${encodeURIComponent(email)}`, {
      method: 'PUT',
      body: JSON.stringify({ password }),
    })
  } catch {
    error.value = 'Could not reset password.'
  }
}

async function remove(email: string) {
  if (!window.confirm(`Delete ${email}? This removes their sessions and messages.`)) return
  try {
    await api(`/api/admin/users/${encodeURIComponent(email)}`, { method: 'DELETE' })
    await load()
  } catch {
    error.value = 'Could not delete user.'
  }
}

function goChat() { window.location.href = '/' }
function logout() { clearSession(); window.location.href = '/login' }
function shortDate(iso: string) { try { return new Date(iso).toLocaleDateString() } catch { return iso } }
</script>

<style scoped>
.admin { max-width: 820px; margin: 0 auto; padding: 1.5rem 1rem; font-family: system-ui, sans-serif; }
.admin__head { display: flex; align-items: center; justify-content: space-between; }
.admin__head h1 { font-size: 1.25rem; margin: 0; }
.admin__actions { display: flex; gap: .5rem; }
.admin__error { color: #dc2626; }
.admin__form { display: flex; gap: .5rem; margin: 1rem 0; flex-wrap: wrap; }
.admin__form input, .admin__form select { padding: .5rem; border-radius: 8px; border: 1px solid #cbd5e1; font: inherit; }
.admin__table { width: 100%; border-collapse: collapse; }
.admin__table th, .admin__table td { text-align: left; padding: .5rem; border-bottom: 1px solid #e2e8f0; }
.admin__rowactions { display: flex; gap: .5rem; justify-content: flex-end; }
.btn { border: 1px solid transparent; border-radius: 8px; padding: .4rem .7rem; cursor: pointer; font: inherit; background: #fff; color: #0f172a; }
.btn--primary { background: #2563eb; color: #fff; }
.btn--ghost { background: transparent; border-color: #cbd5e1; }
</style>
