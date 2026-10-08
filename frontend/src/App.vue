<script setup>
import { onMounted, ref } from 'vue'
import { apiGet } from './api/client'

const state = ref('loading')
const detail = ref(null)

onMounted(async () => {
  try {
    const { ok, body } = await apiGet('/health/')
    state.value = ok ? 'up' : 'degraded'
    detail.value = body
  } catch {
    state.value = 'down'
  }
})
</script>

<template>
  <main class="page">
    <h1>EdgeShield</h1>
    <p class="subtitle">CDN &amp; traffic control plane</p>
    <section class="card">
      <h2>API status</h2>
      <p :class="['badge', state]">{{ state }}</p>
      <pre v-if="detail">{{ detail }}</pre>
    </section>
  </main>
</template>

<style scoped>
.page { max-width: 480px; margin: 4rem auto; padding: 0 1rem; font-family: system-ui, sans-serif; }
.subtitle { color: #666; margin-top: 0; }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem 1.25rem; }
.badge { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; color: #fff; background: #888; }
.badge.up { background: #2e9d52; }
.badge.degraded, .badge.down { background: #c0392b; }
</style>
