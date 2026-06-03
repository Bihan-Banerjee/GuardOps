import { useRef, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { Cpu, MemoryStick, Zap, GitBranch, WifiOff, Activity, CheckCircle, AlertCircle } from 'lucide-react'

function SectionHeader({ children }) {
  const ref = useRef(null)
  const inView = useInView(ref)
  useEffect(() => {
    if (!inView) return
    anime({
      targets: ref.current.querySelectorAll('.sh-letter'),
      translateY: ['-20px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutExpo',
      duration: 500,
      delay: anime.stagger(30),
    })
  }, [inView])
  return (
    <div ref={ref} className="flex items-center gap-4 mb-10">
      <div className="flex gap-0.5 whitespace-pre">
        {children.split('').map((ch, i) => (
          <span key={i} className="sh-letter inline-block font-black text-2xl sm:text-3xl font-mono tracking-[0.15em] opacity-0"
            style={{ color: '#e4e4e7' }}>
            {ch === ' ' ? ' ' : ch}
          </span>
        ))}
      </div>
      <div className="flex-1 h-px bg-gradient-to-r from-terminal/30 to-transparent" />
    </div>
  )
}

function OfflineBanner({ reason }) {
  return (
    <div className="flex flex-col items-center justify-center py-8 gap-2">
      <WifiOff className="w-6 h-6 text-zinc-700" strokeWidth={1} />
      <p className="font-mono text-xs text-zinc-600 text-center break-all line-clamp-3">
        {reason ? reason.split('\n')[0].slice(0, 120) : 'Metrics unavailable'}
      </p>
    </div>
  )
}

function MetricValue({ label, value, unit = '', icon: Icon }) {
  const numVal = Array.isArray(value) && value.length > 0
    ? parseFloat(value[0]?.value?.[1] ?? 0)
    : null

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 text-xs font-mono text-zinc-600">
        {Icon && <Icon className="w-3 h-3" strokeWidth={1.5} />}
        {label}
      </div>
      <div className="text-2xl font-black font-mono text-terminal">
        {numVal !== null ? numVal.toFixed(2) : '—'}
        <span className="text-sm font-normal text-zinc-500 ml-1">{unit}</span>
      </div>
    </div>
  )
}

function PodBar({ label, value, max, unit }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  const barColor = pct > 80 ? 'bg-red-500' : pct > 60 ? 'bg-yellow-500' : 'bg-terminal'

  return (
    <div className="flex items-center gap-3">
      <span className="w-32 text-xs font-mono text-zinc-500 truncate shrink-0">{label}</span>
      <div className="flex-1 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-700 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-16 text-right text-xs font-mono text-zinc-400 shrink-0">
        {value.toFixed(3)}{unit}
      </span>
    </div>
  )
}

export default function Metrics() {
  const ref = useRef(null)
  const inView = useInView(ref)
  const appeared = useRef(false)

  const { data: appData }       = useApi('/api/v1/metrics/app',       {},            { refreshMs: 15_000 })
  const { data: resourceData }  = useApi('/api/v1/metrics/resources', {},            { refreshMs: 15_000 })
  const { data: syncData }      = useApi('/api/v1/sync-status',       { env: 'prod' }, { refreshMs: 30_000 })

  useEffect(() => {
    if (!inView || appeared.current) return
    appeared.current = true
    anime({
      targets: ref.current,
      opacity: [0, 1],
      translateY: ['20px', '0px'],
      easing: 'easeOutExpo',
      duration: 700,
    })
  }, [inView])

  // Parse resource data
  const cpuEntries = (resourceData?.cpu_cores ?? []).map(r => ({
    pod: r.metric?.pod ?? 'pod',
    val: parseFloat(r.value?.[1] ?? 0),
  }))
  const memEntries = (resourceData?.memory_bytes ?? []).map(r => ({
    pod: r.metric?.pod ?? 'pod',
    val: parseFloat(r.value?.[1] ?? 0) / 1024 / 1024, // bytes → MB
  }))
  const maxCpu = Math.max(...cpuEntries.map(e => e.val), 0.001)
  const maxMem = Math.max(...memEntries.map(e => e.val), 1)

  const syncStatus = syncData?.sync_status
  const healthStatus = syncData?.health_status

  return (
    <section id="metrics" className="relative bg-[#030303]/82 py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>LIVE METRICS</SectionHeader>

        <div ref={ref} className="opacity-0 grid lg:grid-cols-3 gap-6">

          {/* App metrics */}
          <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
            <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-6 flex items-center gap-2">
              <Zap className="w-3.5 h-3.5 text-terminal" />
              Application
            </h3>
            {!appData ? (
              <div className="py-4 flex items-center gap-2 text-zinc-600 font-mono text-xs">
                <Activity className="w-3 h-3 animate-spin" /> Querying Prometheus...
              </div>
            ) : !appData.available ? (
              <OfflineBanner reason={appData.reason} />
            ) : (
              <div className="space-y-6">
                <MetricValue
                  label="Request Rate"
                  value={appData.request_rate}
                  unit="req/s"
                  icon={Activity}
                />
                <MetricValue
                  label="P95 Latency"
                  value={appData.request_latency_ms}
                  unit="ms"
                  icon={Zap}
                />
              </div>
            )}
          </div>

          {/* Resource usage */}
          <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
            <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-6 flex items-center gap-2">
              <Cpu className="w-3.5 h-3.5 text-terminal" />
              Resources
            </h3>
            {!resourceData ? (
              <div className="py-4 flex items-center gap-2 text-zinc-600 font-mono text-xs">
                <Activity className="w-3 h-3 animate-spin" /> Querying cluster...
              </div>
            ) : !resourceData.available ? (
              <OfflineBanner reason={resourceData.reason} />
            ) : (
              <div className="space-y-6">
                {cpuEntries.length > 0 && (
                  <div>
                    <p className="text-xs font-mono text-zinc-600 mb-3 flex items-center gap-1.5">
                      <Cpu className="w-3 h-3" /> CPU (cores)
                    </p>
                    <div className="space-y-2">
                      {cpuEntries.map(e => (
                        <PodBar key={e.pod} label={e.pod} value={e.val} max={maxCpu} unit="c" />
                      ))}
                    </div>
                  </div>
                )}
                {memEntries.length > 0 && (
                  <div>
                    <p className="text-xs font-mono text-zinc-600 mb-3 flex items-center gap-1.5">
                      <MemoryStick className="w-3 h-3" /> Memory (MB)
                    </p>
                    <div className="space-y-2">
                      {memEntries.map(e => (
                        <PodBar key={e.pod} label={e.pod} value={e.val} max={maxMem} unit="MB" />
                      ))}
                    </div>
                  </div>
                )}
                {cpuEntries.length === 0 && memEntries.length === 0 && (
                  <p className="text-xs font-mono text-zinc-600">No pod data available</p>
                )}
              </div>
            )}
          </div>

          {/* ArgoCD sync */}
          <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
            <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-6 flex items-center gap-2">
              <GitBranch className="w-3.5 h-3.5 text-terminal" />
              GitOps Sync
            </h3>
            {!syncData ? (
              <div className="py-4 flex items-center gap-2 text-zinc-600 font-mono text-xs">
                <Activity className="w-3 h-3 animate-spin" /> Querying ArgoCD...
              </div>
            ) : !syncData.available ? (
              <OfflineBanner reason={syncData.reason} />
            ) : (
              <div className="space-y-5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-zinc-600">Sync Status</span>
                  <span className={`text-xs font-mono px-2 py-1 rounded border font-bold flex items-center gap-1.5 ${
                    syncStatus === 'Synced'
                      ? 'text-terminal bg-terminal/10 border-terminal/30'
                      : syncStatus === 'OutOfSync'
                      ? 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30'
                      : 'text-zinc-400 bg-zinc-800 border-zinc-700'
                  }`}>
                    {syncStatus === 'Synced'
                      ? <CheckCircle className="w-3 h-3" />
                      : <AlertCircle className="w-3 h-3" />}
                    {syncStatus ?? '—'}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-xs font-mono text-zinc-600">Health</span>
                  <span className={`text-xs font-mono px-2 py-1 rounded border font-bold ${
                    healthStatus === 'Healthy'
                      ? 'text-terminal bg-terminal/10 border-terminal/30'
                      : healthStatus === 'Degraded'
                      ? 'text-red-400 bg-red-500/10 border-red-500/30'
                      : 'text-zinc-400 bg-zinc-800 border-zinc-700'
                  }`}>
                    {healthStatus ?? '—'}
                  </span>
                </div>

                {syncData.revision && (
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-zinc-600">Revision</span>
                    <span className="text-xs font-mono text-zinc-400 bg-zinc-800 px-2 py-1 rounded">
                      {syncData.revision.slice(0, 7)}
                    </span>
                  </div>
                )}

                {syncData.env && (
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-mono text-zinc-600">Environment</span>
                    <span className="text-xs font-mono text-zinc-400">{syncData.env}</span>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
