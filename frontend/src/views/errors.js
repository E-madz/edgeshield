// DRF returns {field: ["msg"]} or {detail: "msg"}; flatten it for display.
export function flattenErrors(body) {
  if (!body || typeof body !== 'object') return ['Something went wrong.']
  return Object.entries(body).flatMap(([field, value]) => {
    const messages = Array.isArray(value) ? value : [String(value)]
    return messages.map((m) => (field === 'detail' || field === 'non_field_errors' ? m : `${field}: ${m}`))
  })
}
