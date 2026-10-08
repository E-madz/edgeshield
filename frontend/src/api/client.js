// Thin wrapper around fetch; auth headers will be added here in slice 1.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

export async function apiGet(path) {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { Accept: 'application/json' },
  })
  const body = await response.json().catch(() => ({}))
  return { ok: response.ok, status: response.status, body }
}
