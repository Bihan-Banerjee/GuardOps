// Tests for the api.js live→snapshot fallback — the core of the "URL works 24/7"
// fix. The module reads VITE_* env and SNAPSHOT_URL at import time, so each test
// resets modules and re-imports after stubbing env + global fetch.
import { describe, it, expect, vi, beforeEach } from 'vitest'

const SNAPSHOT_URL = 'https://snap.example/dashboard/snapshot.json'
const SNAP = {
  generated_at: '2026-06-01T00:00:00.000Z',
  version: '1.0.0',
  data: { '/api/v1/summary': { total_runs: 3 } },
}

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return { ok, status, json: async () => body }
}

async function loadApi() {
  vi.resetModules()
  return import('../api.js')
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
  vi.stubEnv('VITE_SNAPSHOT_URL', SNAPSHOT_URL)
})

describe('apiFetch fallback', () => {
  it('returns live data and stays online when the API responds', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ total_runs: 9 }))
    vi.stubGlobal('fetch', fetchMock)

    const api = await loadApi()
    const data = await api.apiFetch('/api/v1/summary')

    expect(data).toEqual({ total_runs: 9 })
    expect(api.getOfflineState().offline).toBe(false)
    expect(fetchMock).toHaveBeenCalledTimes(1) // never touched the snapshot
  })

  it('falls back to the snapshot and flips offline when the live API fails', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down')) // live call
      .mockResolvedValueOnce(jsonResponse(SNAP)) // snapshot fetch
    vi.stubGlobal('fetch', fetchMock)

    const api = await loadApi()
    const data = await api.apiFetch('/api/v1/summary')

    expect(data).toEqual({ total_runs: 3 })
    expect(api.getOfflineState().offline).toBe(true)
    expect(api.getOfflineState().snapshotTime).toBe(SNAP.generated_at)
  })

  it('throws the original error when the snapshot has no entry for the path', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down')) // live call
      .mockResolvedValueOnce(jsonResponse(SNAP)) // snapshot has no /runs entry
    vi.stubGlobal('fetch', fetchMock)

    const api = await loadApi()
    await expect(api.apiFetch('/api/v1/runs')).rejects.toThrow('network down')
  })

  it('notifies subscribers when the offline state changes', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce(jsonResponse(SNAP))
    vi.stubGlobal('fetch', fetchMock)

    const api = await loadApi()
    const seen = []
    api.subscribeOffline((s) => seen.push(s.offline))
    await api.apiFetch('/api/v1/summary')

    expect(seen[0]).toBe(false) // initial state on subscribe
    expect(seen[seen.length - 1]).toBe(true) // after fallback
  })
})
