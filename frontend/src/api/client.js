// Thin wrapper around fetch. It knows nothing about stores: the auth store
// plugs itself in through configureClient(), which avoids circular imports.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api'

let hooks = {
  getAccessToken: () => null,
  refreshAccessToken: async () => false,
}

export function configureClient(newHooks) {
  hooks = { ...hooks, ...newHooks }
}

async function send(method, path, body, accessToken) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`

  const response = await fetch(`${BASE_URL}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  return { ok: response.ok, status: response.status, body: data }
}

// auth: true  -> attach the access token; on a 401 refresh it once and retry.
export async function apiRequest(method, path, { body, auth = false } = {}) {
  if (!auth) return send(method, path, body)

  let result = await send(method, path, body, hooks.getAccessToken())
  if (result.status === 401 && (await hooks.refreshAccessToken())) {
    result = await send(method, path, body, hooks.getAccessToken())
  }
  return result
}

export const apiGet = (path, options) => apiRequest('GET', path, options)
export const apiPost = (path, body, options) => apiRequest('POST', path, { ...options, body })
