const BASE = import.meta.env.VITE_API_URL || ''
const TOKEN = import.meta.env.VITE_API_TOKEN || ''

function buildHeaders() {
  const h = { 'Content-Type': 'application/json' }
  if (TOKEN) {
    if (TOKEN.includes(':')) {
      h['Authorization'] = 'Basic ' + btoa(TOKEN)
    } else {
      h['Authorization'] = 'Bearer ' + TOKEN
    }
  }
  return h
}

export async function apiFetch(path, params = {}) {
  const url = new URL(BASE + path, window.location.origin)
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v)
  })
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 8000)
  try {
    const resp = await fetch(url.toString(), {
      headers: buildHeaders(),
      signal: controller.signal,
    })
    if (!resp.ok) throw new Error(`API ${resp.status}: ${path}`)
    return resp.json()
  } finally {
    clearTimeout(timer)
  }
}

export const fetchMeta = () => apiFetch('/api/v1/meta')
export const fetchSummary = (project) => apiFetch('/api/v1/summary', { project })
export const fetchTrends = (days, project) => apiFetch('/api/v1/trends', { days, project })
export const fetchRuns = (limit = 10) => apiFetch('/api/v1/runs', { limit })
export const fetchRunDetail = (id) => apiFetch(`/api/v1/runs/${id}`)
export const fetchFindings = ({ severity, tool, cve, limit = 200 } = {}) =>
  apiFetch('/api/v1/findings', { severity, tool, cve, limit })
export const fetchMetricsApp = () => apiFetch('/api/v1/metrics/app')
export const fetchMetricsResources = (namespace) => apiFetch('/api/v1/metrics/resources', { namespace })
export const fetchRuntimeAlerts = (since = '1h', severity) =>
  apiFetch('/api/v1/runtime/alerts', { since, severity })
export const fetchQuarantine = (namespace) => apiFetch('/api/v1/quarantine', { namespace })
export const fetchSyncStatus = (env = 'prod') => apiFetch('/api/v1/sync-status', { env })
