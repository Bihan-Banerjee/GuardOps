import { useState, useEffect, useRef } from 'react'
import { apiFetch } from '../api.js'

export function useApi(path, params = {}, options = {}) {
  const { refreshMs = 30_000, enabled = true } = options
  const [state, setState] = useState({ data: null, loading: true, error: null })
  const paramsKey = JSON.stringify(params)

  useEffect(() => {
    if (!enabled || !path) return
    let cancelled = false

    let currentRefreshMs = refreshMs

    const fetchData = async () => {
      try {
        const data = await apiFetch(path, params)
        if (!cancelled) {
          setState({ data, loading: false, error: null })
          currentRefreshMs = refreshMs // reset backoff on success
        }
      } catch (err) {
        if (!cancelled) {
          setState(s => ({ ...s, loading: false, error: err.message }))
          currentRefreshMs = Math.min(currentRefreshMs * 2, 120_000) // exponential backoff
        }
      }
    }

    setState(s => ({ ...s, loading: true }))
    fetchData()
    const timer = setInterval(fetchData, refreshMs)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [path, paramsKey, refreshMs, enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  return state
}
