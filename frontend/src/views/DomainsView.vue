<script setup>
import { onMounted, ref, watch } from 'vue'
import { apiDelete, apiGet, apiPost } from '../api/client'
import { flattenErrors } from './errors'

const domains = ref([])
const count = ref(0)
const page = ref(1)
const hasNext = ref(false)
const search = ref('')
const newName = ref('')
const errors = ref([])
const loading = ref(false)

async function load() {
  loading.value = true
  const query = new URLSearchParams({ page: page.value, page_size: 10 })
  if (search.value) query.set('search', search.value)
  const { ok, status, body } = await apiGet(`/domains/?${query}`, { auth: true })
  if (ok) {
    domains.value = body.results
    count.value = body.count
    hasNext.value = !!body.next
  } else if (status === 404 && page.value > 1) {
    page.value -= 1 // we deleted the last row of the last page
  }
  loading.value = false
}

async function create() {
  errors.value = []
  const { ok, body } = await apiPost('/domains/', { name: newName.value }, { auth: true })
  if (!ok) {
    errors.value = flattenErrors(body)
    return
  }
  newName.value = ''
  page.value = 1
  await load()
}

async function remove(domain) {
  if (!confirm(`Delete ${domain.name} and all its origins?`)) return
  await apiDelete(`/domains/${domain.id}/`, { auth: true })
  await load()
}

let timer
watch(search, () => {
  clearTimeout(timer)
  timer = setTimeout(() => {
    page.value = 1
    load()
  }, 300)
})
watch(page, load)
onMounted(load)
</script>

<template>
  <section class="card">
    <h2>Domains</h2>
    <form class="row" @submit.prevent="create">
      <input v-model="newName" placeholder="www.example.com" required />
      <button>Add domain</button>
    </form>
    <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
  </section>

  <section class="card">
    <input v-model="search" placeholder="Search..." />
    <p v-if="!loading && !domains.length" class="muted">No domains yet.</p>
    <ul class="list">
      <li v-for="d in domains" :key="d.id">
        <span>
          <RouterLink :to="{ name: 'domain-detail', params: { id: d.id } }">{{ d.name }}</RouterLink>
          <span class="muted"> &middot; {{ d.origins_count }} origin(s)<span v-if="!d.is_active"> &middot; inactive</span></span>
        </span>
        <button class="danger" @click="remove(d)">Delete</button>
      </li>
    </ul>
    <div class="row">
      <button class="secondary" :disabled="page === 1" @click="page--">Prev</button>
      <span class="muted">Page {{ page }} &middot; {{ count }} total</span>
      <button class="secondary" :disabled="!hasNext" @click="page++">Next</button>
    </div>
    <p><RouterLink :to="{ name: 'home' }">&larr; Back</RouterLink></p>
  </section>
</template>
