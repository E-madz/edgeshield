<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const health = ref('loading')

onMounted(async () => {
  try {
    const { ok } = await apiGet('/health/')
    health.value = ok ? 'up' : 'degraded'
  } catch {
    health.value = 'down'
  }
})

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <section class="card">
    <h2>API status</h2>
    <p :class="['badge', health]">{{ health }}</p>
  </section>
  <section class="card">
    <h2>Your account</h2>
    <p v-if="auth.user">Signed in as <strong>{{ auth.user.email }}</strong></p>
    <p v-else class="error">Could not load your profile (is <code>GET /api/auth/me/</code> implemented yet?)</p>
    <p><RouterLink :to="{ name: 'domains' }">Manage domains &rarr;</RouterLink></p>
    <button @click="logout">Log out</button>
  </section>
</template>
