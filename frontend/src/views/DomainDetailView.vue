<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiDelete, apiGet, apiPatch, apiPost } from '../api/client'
import { flattenErrors } from './errors'
import CachePolicyForm from '../components/CachePolicyForm.vue'
import RateLimitForm from '../components/RateLimitForm.vue'
import PurgePanel from '../components/PurgePanel.vue'

const route = useRoute()
const router = useRouter()
const domain = ref(null)
const notFound = ref(false)
const errors = ref([])
const form = reactive({ host: '', port: 443, scheme: 'https', weight: 1 })

async function load() {
  const { ok, body } = await apiGet(`/domains/${route.params.id}/`, { auth: true })
  if (ok) domain.value = body
  else notFound.value = true
}

async function addOrigin() {
  errors.value = []
  const { ok, body } = await apiPost(`/domains/${route.params.id}/origins/`, { ...form }, { auth: true })
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  form.host = ''
  await load()
}

async function removeOrigin(origin) {
  await apiDelete(`/domains/${route.params.id}/origins/${origin.id}/`, { auth: true })
  await load()
}

async function toggleActive() {
  await apiPatch(`/domains/${route.params.id}/`, { is_active: !domain.value.is_active }, { auth: true })
  await load()
}

async function removeDomain() {
  if (!confirm(`Delete ${domain.value.name}?`)) return
  await apiDelete(`/domains/${route.params.id}/`, { auth: true })
  router.push({ name: 'domains' })
}

onMounted(load)
</script>

<template>
  <p v-if="notFound" class="error">Domain not found.</p>
  <template v-else-if="domain">
    <section class="card">
      <h2>{{ domain.name }}</h2>
      <p class="muted">Status: {{ domain.is_active ? 'active' : 'inactive' }}</p>
      <div class="row">
        <button class="secondary" @click="toggleActive">{{ domain.is_active ? 'Deactivate' : 'Activate' }}</button>
        <button class="danger" @click="removeDomain">Delete domain</button>
      </div>
    </section>

    <section class="card">
      <h3>Origins</h3>
      <p v-if="!domain.origins.length" class="muted">No origins yet.</p>
      <ul class="list">
        <li v-for="o in domain.origins" :key="o.id">
          <span>{{ o.scheme }}://{{ o.host }}:{{ o.port }} <span class="muted">weight {{ o.weight }}</span></span>
          <button class="danger" @click="removeOrigin(o)">Remove</button>
        </li>
      </ul>
      <form class="row" @submit.prevent="addOrigin">
        <input v-model="form.host" placeholder="origin.example.com" required />
        <select v-model="form.scheme"><option>https</option><option>http</option></select>
        <input v-model.number="form.port" type="number" min="1" max="65535" style="width: 5rem" />
        <input v-model.number="form.weight" type="number" min="1" max="100" style="width: 4rem" title="weight" />
        <button>Add</button>
      </form>
      <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
    </section>
    <CachePolicyForm :domain-id="route.params.id" />
    <RateLimitForm :domain-id="route.params.id" />
    <PurgePanel :domain-id="route.params.id" />
    <p><RouterLink :to="{ name: 'domains' }">&larr; All domains</RouterLink></p>
  </template>
</template>
