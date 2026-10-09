<script setup>
import { ref } from 'vue'
import { apiPost } from '../api/client'
import { flattenErrors } from '../views/errors'

const props = defineProps({ domainId: { type: [String, Number], required: true } })

const EXAMPLE = ['/index.html 0', '/index.html 10', '/logo.png 11', '/index.html 120', '/index.html 130', '/admin 131', '/logo.png 4000'].join('\n')

const text = ref(EXAMPLE)
const capacity = ref(100)
const result = ref(null)
const errors = ref([])

function parse() {
  return text.value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const i = line.lastIndexOf(' ')
      return { path: line.slice(0, i).trim(), at: Number(line.slice(i + 1)) }
    })
}

async function run() {
  errors.value = []
  const items = parse()
  if (items.some((r) => Number.isNaN(r.at))) {
    errors.value = ['Each line must be "<path> <seconds>", e.g. "/index.html 10".']
    return
  }
  const { ok, body } = await apiPost(
    `/domains/${props.domainId}/cache-policy/simulate/`,
    { capacity: capacity.value, requests: items },
    { auth: true },
  )
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  result.value = body
}
</script>

<template>
  <div class="sandbox">
    <h4>Simulator</h4>
    <p class="muted">Replays requests (one per line: <code>path seconds</code>) against an LRU cache using the <strong>saved</strong> policy.</p>
    <textarea v-model="text" rows="6" style="width: 100%; font-family: monospace"></textarea>
    <div class="row">
      <label>Cache capacity (objects) <input v-model.number="capacity" type="number" min="1" max="1000" style="width: 5rem" /></label>
      <button type="button" @click="run">Run simulation</button>
    </div>
    <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
    <template v-if="result">
      <p>
        Hit ratio <strong>{{ Math.round(result.summary.hit_ratio * 100) }}%</strong> &middot;
        origin requests <strong>{{ result.summary.origin_requests }}</strong> of {{ result.summary.requests }} &middot;
        evictions {{ result.summary.evictions }}
      </p>
      <ul class="list">
        <li v-for="(r, i) in result.results" :key="i">
          <span><code>{{ r.path }}</code> <span class="muted">@ {{ r.at }}s</span></span>
          <span :class="['tag', r.outcome.toLowerCase()]" :title="r.reason || ''">{{ r.outcome }}</span>
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.sandbox { border-top: 1px dashed #ccc; margin-top: 1rem; padding-top: 0.5rem; }
.tag { padding: 0.1rem 0.5rem; border-radius: 4px; color: #fff; font-size: 0.8rem; background: #888; }
.tag.hit { background: #2e9d52; }
.tag.stale { background: #d68910; }
.tag.miss { background: #2457d6; }
</style>
