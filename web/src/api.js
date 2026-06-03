// API client for the GuardOps dashboard SPA.
//
// The live API (app.guardops.live) only answers while the EKS cluster is up — it
// is torn down nightly to stay near-zero cost. So every request tries the live API
// first and, on failure, falls back to a static snapshot published to a public URL
// (VITE_SNAPSHOT_URL, written by `guardops dashboard snapshot --to-s3`). That keeps
// the site working 24/7 from any device. When a fallback is used we broadcast an
// "offline" signal so the UI can show a banner with the snapshot timestamp.

const BASE = import.meta.env.VITE_API_URL || ''
const TOKEN = import.meta.env.VITE_API_TOKEN || ''
const SNAPSHOT_URL = import.meta.env.VITE_SNAPSHOT_URL || ''

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

// ── offline signal (live API unreachable → serving snapshot) ──────────────────
const offlineListeners = new Set()
let offlineState = { offline: false, snapshotTime: null }

export function getOfflineState() {
  return offlineState
}

export function subscribeOffline(fn) {
  offlineListeners.add(fn)
  fn(offlineState)
  return () => offlineListeners.delete(fn)
}

function setOffline(next) {
  const merged = { ...offlineState, ...next }
  if (merged.offline === offlineState.offline && merged.snapshotTime === offlineState.snapshotTime) {
    return // no change — don't churn subscribers
  }
  offlineState = merged
  offlineListeners.forEach((fn) => fn(offlineState))
}

// ── snapshot fallback (fetched once, then cached for the page lifetime) ────────
let snapshotCache = null
let snapshotPromise = null

async function loadSnapshot() {
  if (snapshotCache) return snapshotCache
  if (!SNAPSHOT_URL) return null
  if (!snapshotPromise) {
    snapshotPromise = fetch(SNAPSHOT_URL)
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
  }
  snapshotCache = await snapshotPromise
  return snapshotCache
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
    const data = await resp.json()
    setOffline({ offline: false, snapshotTime: null }) // a live read succeeded
    return data
  } catch (err) {
    const snap = await loadSnapshot()
    if (snap && snap.data && Object.prototype.hasOwnProperty.call(snap.data, path)) {
      setOffline({ offline: true, snapshotTime: snap.generated_at || null })
      return snap.data[path]
    }
    throw err // no snapshot, or no entry for this path (e.g. a run detail)
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
