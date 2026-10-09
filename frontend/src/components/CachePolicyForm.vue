<script setup>
import { onMounted, reactive, ref } from 'vue'
import { apiGet, apiPatch } from '../api/client'
import { flattenErrors } from '../views/errors'

const props = defineProps({ domainId: { type: [String, Number], required: true } })
const url = `/domains/${props.domainId}/cache-policy/`

const form = reactive({
  enabled: true,
  default_ttl_seconds: 3600,
  browser_ttl_seconds: 0,
  serve_stale_seconds: 0,
  query_string_mode: 'ignore',
  bypassText: '',
})
const errors = ref([])
const saved = ref(false)

onMounted(async () => {
  const { ok, body } = await apiGet(url, { auth: true })
  if (!ok) return
  Object.assign(form, body, { bypassText: body.bypass_paths.join('\n') })
})

async function save() {
  errors.value = []
  saved.value = false
  const { bypassText, ...rest } = form
  const payload = { ...rest, bypass_paths: bypassText.split('\n').map((p) => p.trim()).filter(Boolean) }
  const { ok, body } = await apiPatch(url, payload, { auth: true })
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  form.bypassText = body.bypass_paths.join('\n')
  saved.value = true
}
</script>

<template>
  <section class="card">
    <h3>Cache policy</h3>
    <form @submit.prevent="save">
      <label class="row"><input v-model="form.enabled" type="checkbox" /> Caching enabled</label>
      <label class="field">Edge TTL (seconds)
        <input v-model.number="form.default_ttl_seconds" type="number" min="0" max="31536000" />
      </label>
      <label class="field">Browser TTL (seconds)
        <input v-model.number="form.browser_ttl_seconds" type="number" min="0" max="31536000" />
      </label>
      <label class="field">Serve stale while revalidating (seconds)
        <input v-model.number="form.serve_stale_seconds" type="number" min="0" max="604800" />
      </label>
      <label class="field">Query string
        <select v-model="form.query_string_mode">
          <option value="ignore">Ignore (same object for any query)</option>
          <option value="include">Include in cache key</option>
        </select>
      </label>
      <label class="field">Never cache these path prefixes (one per line)
        <textarea v-model="form.bypassText" rows="3" placeholder="/admin&#10;/api/private"></textarea>
      </label>
      <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
      <div class="row">
        <button>Save cache policy</button>
        <span v-if="saved" class="muted">Saved</span>
      </div>
    </form>
  </section>
</template>
