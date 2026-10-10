import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { apiGet, apiPost, configureClient } from '../api/client'

const REFRESH_KEY = 'edgeshield.refresh'

// localStorage can throw (private mode, blocked site data): never let it break the app.
function readRefresh() {
  try {
    return localStorage.getItem(REFRESH_KEY)
  } catch {
    return null
  }
}
function writeRefresh(value) {
  try {
    if (value) localStorage.setItem(REFRESH_KEY, value)
    else localStorage.removeItem(REFRESH_KEY)
  } catch {
    /* ignore */
  }
}

export const useAuthStore = defineStore('auth', () => {
  // Access token lives only in memory: a page reload drops it, and a stolen
  // copy from storage is impossible. The refresh token brings it back.
  const accessToken = ref(null)
  const user = ref(null)
  const initialized = ref(false)
  const isAuthenticated = computed(() => !!accessToken.value)

  let refreshInFlight = null

  // Several requests can hit 401 together; they must share ONE refresh call.
  function refreshAccessToken() {
    if (refreshInFlight) return refreshInFlight
    const refresh = readRefresh()
    if (!refresh) return Promise.resolve(false)

    refreshInFlight = apiPost('/auth/refresh/', { refresh })
      .then(({ ok, body }) => {
        if (!ok) {
          clearSession()
          return false
        }
        accessToken.value = body.access
        return true
      })
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null
      })
    return refreshInFlight
  }

  function clearSession() {
    accessToken.value = null
    user.value = null
    writeRefresh(null)
  }

  configureClient({
    getAccessToken: () => accessToken.value,
    refreshAccessToken,
  })

  async function login(email, password) {
    const result = await apiPost('/auth/login/', { email, password })
    if (result.ok) {
      accessToken.value = result.body.access
      writeRefresh(result.body.refresh)
      await loadUser()
    }
    return result
  }

  async function register(email, password) {
    return apiPost('/auth/register/', { email, password })
  }

  async function loadUser() {
    const result = await apiGet('/auth/me/', { auth: true })
    user.value = result.ok ? result.body : null
    return result
  }

  function logout() {
    clearSession()
  }

  // Called once at startup: a stored refresh token silently restores the session.
  async function init() {
    if (initialized.value) return
    if (readRefresh() && (await refreshAccessToken())) await loadUser()
    initialized.value = true
  }

  return { accessToken, user, isAuthenticated, initialized, login, register, loadUser, logout, init }
})
