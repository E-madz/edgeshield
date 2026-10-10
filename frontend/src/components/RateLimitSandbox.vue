<script setup>
import { ref } from 'vue'
import { apiDelete, apiPost } from '../api/client'
import { flattenErrors } from '../views/errors'

const props = defineProps({ domainId: { type: [String, Number], required: true } })
const base = `/domains/${props.domainId}/rate-limit-policy/check/`

const clientIp = ref('203.0.113.10')
const requests = ref(10)
const result = ref(null)
const errors = ref([])

async function send() {
  errors.value = []
  const { ok, body } = await apiPost(
    base,
    { client_ip: clientIp.value || undefined, requests: requests.value },
    { auth: true },
  )
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  result.value = body
}

async function reset() {
  errors.value = []
  const { ok, body } = await apiDelete(`${base}?client_ip=${encodeURIComponent(clientIp.value)}`, { auth: true })
  if (!ok) errors.value = flattenErrors(body)
  else result.value = null
}
</script>

<template>
  <div class="sandbox">
    <h4>Sandbox</h4>
    <p class="muted">Sends simulated requests through the <strong>saved</strong> policy using Redis. State persists between clicks, so wait a few seconds to watch tokens refill.</p>
    <div class="row">
      <input v-model="clientIp" placeholder="client IP" />
      <input v-model.number="requests" type="number" min="1" max="1000" style="width: 5rem" />
      <button type="button" @click="send">Send requests</button>
      <button type="button" class="secondary" @click="reset">Reset</button>
    </div>
    <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
    <template v-if="result">
      <p v-if="!result.enabled" class="muted">{{ result.detail }}</p>
      <template v-else>
        <p>
          <strong>{{ result.summary.allowed }}</strong> allowed,
          <strong>{{ result.summary.limited }}</strong> over the limit
          <span v-if="result.action === 'log_only'" class="muted">(monitor mode: not blocked)</span>
        </p>
        <div class="strip">
          <span
            v-for="r in result.results"
            :key="r.n"
            :class="['cell', r.allowed ? 'ok' : 'bad']"
            :title="`#${r.n}: ${r.status}, remaining ${r.remaining}, retry in ${r.retry_after}s`"
          ></span>
        </div>
        <p v-if="result.truncated" class="muted">Showing the first 50 requests.</p>
      </template>
    </template>
  </div>
</template>

<style scoped>
.sandbox { border-top: 1px dashed #ccc; margin-top: 1rem; padding-top: 0.5rem; }
.strip { display: flex; flex-wrap: wrap; gap: 3px; }
.cell { width: 14px; height: 14px; border-radius: 3px; background: #888; }
.cell.ok { background: #2e9d52; }
.cell.bad { background: #c0392b; }
</style>
