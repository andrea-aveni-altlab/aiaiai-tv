const BASE = import.meta.env.VITE_API_URL || ''

async function apiFetch(path, opts = {}) {
  const res = await fetch(`${BASE}${path}`, opts)
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  status:    () => apiFetch('/api/status'),
  targets:   () => apiFetch('/api/targets'),
  emittenti: () => apiFetch('/api/emittenti'),

  programmiGiorno: (data, target, emit) => {
    const params = new URLSearchParams({ target })
    if (emit) params.set('emit', emit)
    return apiFetch(`/api/programmi/${data}?${params}`)
  },

  primeTime: (data, target) =>
    apiFetch(`/api/primetime/${data}?target=${target}`),

  top: (data, target, n = 20, fascia = null) => {
    const params = new URLSearchParams({ target, n })
    if (fascia) params.set('fascia', fascia)
    return apiFetch(`/api/top/${data}?${params}`)
  },

  storico: (titolo, target, from, to, emit) => {
    const params = new URLSearchParams({ target })
    if (from)  params.set('from', from)
    if (to)    params.set('to', to)
    if (emit)  params.set('emit', emit)
    return apiFetch(`/api/programma/${encodeURIComponent(titolo)}?${params}`)
  },

  search: (q) => apiFetch(`/api/search?q=${encodeURIComponent(q)}`),

  nl: (q, data, target) =>
    apiFetch('/api/nl', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ q, data, target }),
    }),
}
