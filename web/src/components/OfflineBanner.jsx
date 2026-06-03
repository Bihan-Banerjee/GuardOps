import { CloudOff } from 'lucide-react'
import { useOffline } from '../hooks/useOffline.js'

// Renders only when the live API is unreachable and the SPA is serving the static
// snapshot (api.js fallback). The GuardOps cluster is torn down nightly, so this is
// an expected state — the banner explains it and shows how fresh the data is rather
// than letting the dashboard look broken.
function formatWhen(iso) {
  if (!iso) return 'an earlier run'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return 'an earlier run'
  return d.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export default function OfflineBanner() {
  const { offline, snapshotTime } = useOffline()
  if (!offline) return null

  return (
    <div
      role="status"
      className="fixed top-16 left-0 right-0 z-40 bg-amber-500/10 border-b border-amber-500/30 backdrop-blur-md"
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-2 flex items-center justify-center gap-2 text-center">
        <CloudOff className="w-3.5 h-3.5 text-amber-400 shrink-0" strokeWidth={1.75} />
        <p className="text-xs font-mono text-amber-300/90">
          Live backend offline — showing cached data from{' '}
          <span className="text-amber-200 font-semibold">{formatWhen(snapshotTime)}</span>.
          The GuardOps cluster spins up on demand.
        </p>
      </div>
    </div>
  )
}
