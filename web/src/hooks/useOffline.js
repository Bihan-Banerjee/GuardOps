import { useState, useEffect } from 'react'
import { subscribeOffline } from '../api.js'

// Subscribes to the api.js offline signal: { offline, snapshotTime }. `offline`
// flips true the first time a live request falls back to the static snapshot, and
// back to false once any live request succeeds again. `snapshotTime` is the
// snapshot's generated_at (ISO string) so the banner can show data freshness.
export function useOffline() {
  const [state, setState] = useState({ offline: false, snapshotTime: null, status: 'live' })
  useEffect(() => subscribeOffline(setState), [])
  return state
}
