<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { flattenErrors } from './errors'

const auth = useAuthStore()
const router = useRouter()
const email = ref('')
const password = ref('')
const errors = ref([])
const loading = ref(false)

async function submit() {
  loading.value = true
  errors.value = []
  try {
    const { ok, body } = await auth.login(email.value, password.value)
    if (ok) router.push({ name: 'home' })
    else errors.value = flattenErrors(body)
  } catch {
    errors.value = ['Cannot reach the server.']
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <h2>Log in</h2>
    <label class="field">Email <input v-model="email" type="email" required autocomplete="email" /></label>
    <label class="field">Password <input v-model="password" type="password" required autocomplete="current-password" /></label>
    <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
    <button :disabled="loading">{{ loading ? '...' : 'Log in' }}</button>
    <p>No account? <RouterLink :to="{ name: 'register' }">Register</RouterLink></p>
  </form>
</template>
