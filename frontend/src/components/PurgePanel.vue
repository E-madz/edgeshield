<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { apiGet, apiPost } from '../api/client'
import { flattenErrors } from '../views/errors'

const props = defineProps({ domainId: { type: [String, Number], required: true } })
const base = `/domains/${props.domainId}`

const SAMPLE = ['/index.html', '/img/logo.png', '/blog/post-1', '/blog/post-2', '/about'].join('\n')

const objects = ref([])
const seedText = ref(SAMPLE)
const kind = ref('paths')
const pathsText = ref('/blog/*')
// One key per intended purge: a retry or double click re-sends the SAME key and
// the server answers with the original job instead of starting another one.
const idempotencyKey = ref(crypto.randomUUID())
const jobs = ref([])
const errors = ref([])
const notice = ref('')
let timer = null

const hasActive = computed(() => jobs.value.some((j) => ['pending', 'running', 'retrying'].includes(j.status)))

function lines(text) {
  return text.split('\n').map((l) => l.trim()).filter(Boolean)
}

async function loadObjects() {
  const { ok, body } = await apiGet(`${base}/cache-objects/`, { auth: true })
  if (ok) objects.value = body.paths
}

async function loadJobs() {
  const { ok, body } = await apiGet(`${base}/purges/?page_size=10`, { auth: true })
  if (ok) jobs.value = body.results
  if (!hasActive.value) {
    stopPolling()
    await loadObjects() // the purge may just have finished
  }
}

function startPolling() {
  if (!timer) timer = setInterval(loadJobs, 1000)
}
function stopPolling() {
  clearInterval(timer)
  timer = null
}

async function seed() {
  errors.value = []
  const { ok, body } = await apiPost(`${base}/cache-objects/`, { paths: lines(seedText.value) }, { auth: true })
  if (!ok) errors.value = flattenErrors(body)
  await loadObjects()
}

async function purge() {
  errors.value = []
  notice.value = ''
  const payload = kind.value === 'all' ? { kind: 'all' } : { kind: 'paths', paths: lines(pathsText.value) }
  const { ok, status, body, headers } = await apiPost(`${base}/purges/`, payload, {
    auth: true,
    headers: { 'Idempotency-Key': idempotencyKey.value },
  })
  if (!ok) {
    errors.value = flattenErrors(body.job ? { detail: body.detail } : body)
    // 409 means this key already belongs to another request: it must not be reused.
    if (status === 409) idempotencyKey.value = crypto.randomUUID()
    return
  }
  if (headers.get('Idempotent-Replayed')) notice.value = 'Same request detected: showing the original job.'
  idempotencyKey.value = crypto.randomUUID()
  await loadJobs()
  startPolling()
}

onMounted(async () => {
  await loadObjects()
  await loadJobs()
  if (hasActive.value) startPolling()
})
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="card">
    <h3>Edge cache &amp; purge</h3>

    <h4>Cached objects ({{ objects.length }})</h4>
    <p v-if="!objects.length" class="muted">Nothing cached. Add sample objects to try a purge.</p>
    <p v-else class="muted">{{ objects.join('  ·  ') }}</p>
    <details>
      <summary>Add sample objects</summary>
      <textarea v-model="seedText" rows="4" style="width: 100%; font-family: monospace"></textarea>
      <button type="button" class="secondary" @click="seed">Add to cache</button>
    </details>

    <h4>Purge</h4>
    <form @submit.prevent="purge">
      <div class="row">
        <label><input v-model="kind" type="radio" value="paths" /> Paths</label>
        <label><input v-model="kind" type="radio" value="all" /> Everything</label>
      </div>
      <textarea
        v-if="kind === 'paths'"
        v-model="pathsText"
        rows="3"
        style="width: 100%; font-family: monospace"
        placeholder="/img/logo.png&#10;/blog/*"
      ></textarea>
      <p class="muted">Idempotency-Key: <code>{{ idempotencyKey.slice(0, 8) }}…</code></p>
      <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
      <p v-if="notice" class="muted">{{ notice }}</p>
      <button>Purge</button>
    </form>

    <h4>Jobs</h4>
    <p v-if="!jobs.length" class="muted">No purge jobs yet.</p>
    <ul class="list">
      <li v-for="j in jobs" :key="j.id">
        <span>
          #{{ j.id }} <span class="muted">{{ j.kind === 'all' ? 'everything' : j.paths.join(', ') }}</span>
          <span v-if="j.purged_count !== null" class="muted"> &middot; {{ j.purged_count }} removed</span>
          <span v-if="j.attempts > 1" class="muted"> &middot; {{ j.attempts }} attempts</span>
          <span v-if="j.error" class="error"> {{ j.error }}</span>
        </span>
        <span :class="['tag', j.status]">{{ j.status }}</span>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.tag { padding: 0.1rem 0.5rem; border-radius: 4px; color: #fff; font-size: 0.8rem; background: #888; }
.tag.succeeded { background: #2e9d52; }
.tag.failed { background: #c0392b; }
.tag.running, .tag.retrying { background: #d68910; }
.tag.pending { background: #2457d6; }
</style>
