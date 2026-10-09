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
    const reg = await auth.register(email.value, password.value)
    if (!reg.ok) {
      errors.value = flattenErrors(reg.body)
      return
    }
    const login = await auth.login(email.value, password.value)
    if (login.ok) router.push({ name: 'home' })
    else errors.value = flattenErrors(login.body)
  } catch {
    errors.value = ['Cannot reach the server.']
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <form class="card" @submit.prevent="submit">
    <h2>Create account</h2>
    <label class="field">Email <input v-model="email" type="email" required autocomplete="email" /></label>
    <label class="field">Password <input v-model="password" type="password" required autocomplete="new-password" /></label>
    <p v-for="e in errors" :key="e" class="error">{{ e }}</p>
    <button :disabled="loading">{{ loading ? '...' : 'Register' }}</button>
    <p>Already registered? <RouterLink :to="{ name: 'login' }">Log in</RouterLink></p>
  </form>
</template>
