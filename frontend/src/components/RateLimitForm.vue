<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { apiGet, apiPatch } from '../api/client'
import { flattenErrors } from '../views/errors'
import RateLimitSandbox from './RateLimitSandbox.vue'

const props = defineProps({ domainId: { type: [String, Number], required: true } })
const url = `/domains/${props.domainId}/rate-limit-policy/`

const form = reactive({
  enabled: false,
  algorithm: 'token_bucket',
  limit: 100,
  window_seconds: 60,
  burst: null,
  scope: 'per_ip',
  action: 'block',
})
const errors = ref([])
const saved = ref(false)

const isBucket = computed(() => form.algorithm === 'token_bucket')
const summary = computed(() => {
  const rate = `${form.limit} requests / ${form.window_seconds}s`
  return isBucket.value
    ? `${rate}, bursts up to ${form.burst || form.limit}`
    : `${rate}, counted over a sliding window`
})

onMounted(async () => {
  const { ok, body } = await apiGet(url, { auth: true })
  if (ok) Object.assign(form, body)
})

async function save() {
  errors.value = []
  saved.value = false
  const { refill_rate_per_second, updated_at, ...rest } = form
  const payload = { ...rest, burst: isBucket.value && form.burst ? form.burst : null }
  const { ok, body } = await apiPatch(url, payload, { auth: true })
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  Object.assign(form, body)
  saved.value = true
}
</script>

<template>
  <section class="card">
    <h3>Rate limiting</h3>
    <form @submit.prevent="save">
      <label class="row"><input v-model="form.enabled" type="checkbox" /> Rate limiting enabled</label>
      <label class="field">Algorithm
        <select v-model="form.algorithm">
          <option value="token_bucket">Token bucket (allows bursts)</option>
          <option value="sliding_window">Sliding window (strict count)</option>
        </select>
      </label>
      <div class="row">
        <label class="field">Requests
          <input v-model.number="form.limit" type="number" min="1" style="width: 6rem" />
        </label>
        <label class="field">per (seconds)
          <input v-model.number="form.window_seconds" type="number" min="1" style="width: 6rem" />
        </label>
        <label v-if="isBucket" class="field">Burst capacity
          <input v-model.number="form.burst" type="number" min="1" placeholder="= requests" style="width: 7rem" />
        </label>
      </div>
      <div class="row">
        <label class="field">Scope
          <select v-model="form.scope">
            <option value="per_ip">Per client IP</option>
            <option value="global">Whole domain</option>
          </select>
        </label>
        <label class="field">When exceeded
          <select v-model="form.action">
            <option value="block">Reject with 429</option>
            <option value="log_only">Only record (monitor)</option>
          </select>
        </label>
      </div>
      <p class="muted">{{ summary }}</p>
      <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
      <div class="row">
        <button>Save rate limit</button>
        <span v-if="saved" class="muted">Saved</span>
      </div>
    </form>
    <RateLimitSandbox :domain-id="domainId" />
  </section>
</template>
