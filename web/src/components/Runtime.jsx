import { useRef, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { AlertTriangle, Shield, WifiOff, Activity } from 'lucide-react'

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
    <div className="border border-zinc-800 rounded-xl bg-zinc-900/30 p-8 text-center">
      <WifiOff className="w-8 h-8 text-zinc-700 mx-auto mb-3" strokeWidth={1} />
      <p className="font-mono text-sm font-bold text-zinc-500 mb-1">CLUSTER OFFLINE</p>
      <p className="font-mono text-xs text-zinc-600 break-all line-clamp-2">
        {reason ? reason.split('\n')[0].slice(0, 100) : 'Data source unavailable'}
      </p>
      <p className="font-mono text-xs text-zinc-700 mt-2">
        Nightly shutdown active — cluster resumes mornings
      </p>
    </div>
  )
}

const SEV_STYLES = {
  CRITICAL: 'text-red-400 bg-red-500/10 border-red-500/30',
  HIGH:     'text-orange-400 bg-orange-500/10 border-orange-500/30',
  MEDIUM:   'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  LOW:      'text-blue-400 bg-blue-500/10 border-blue-500/30',
}

function AlertFeed({ alerts }) {
  if (!alerts?.length) {
    return (
      <div className="py-8 text-center text-zinc-600 font-mono text-sm">
        No alerts in this window
      </div>
    )
  }
  return (
    <div className="space-y-2">
      {alerts.map((alert, i) => (
        <div key={i} className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-4 py-3 border-b border-zinc-800/50 last:border-0">
          <span className="text-xs font-mono text-zinc-600 whitespace-nowrap shrink-0">
            {alert.timestamp ? new Date(alert.timestamp).toLocaleTimeString() : '—'}
          </span>
          <span className={`text-xs font-mono px-2 py-0.5 rounded border self-start shrink-0 ${SEV_STYLES[alert.severity] ?? SEV_STYLES.LOW}`}>
            {alert.severity}
          </span>
          <span className="text-xs font-mono text-zinc-300 font-medium">{alert.rule}</span>
          {alert.pod && (
            <span className="text-xs font-mono text-zinc-600 truncate">{alert.pod}</span>
          )}
        </div>
      ))}
    </div>
  )
}

export default function Runtime() {
  const ref = useRef(null)
  const inView = useInView(ref)
  const appeared = useRef(false)

  const { data: alertsData } = useApi(
    '/api/v1/runtime/alerts',
    { since: '1h' },
    { refreshMs: 15_000 }
  )
  const { data: quarantineData } = useApi(
    '/api/v1/quarantine',
    {},
    { refreshMs: 20_000 }
  )

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

  const counts = alertsData?.counts ?? {}

  return (
    <section id="runtime" className="relative bg-[#030303] py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>RUNTIME SECURITY</SectionHeader>

        <div ref={ref} className="opacity-0 grid lg:grid-cols-2 gap-6">
          {/* Falco alerts */}
          <div>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-mono text-zinc-400 flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 text-orange-400" strokeWidth={1.5} />
                Falco Alerts (last 1h)
              </h3>
              {alertsData?.available && (
                <div className="flex items-center gap-3 text-xs font-mono">
                  {counts.CRITICAL > 0 && <span className="text-red-400">{counts.CRITICAL} CRIT</span>}
                  {counts.HIGH > 0 && <span className="text-orange-400">{counts.HIGH} HIGH</span>}
                  {counts.MEDIUM > 0 && <span className="text-yellow-400">{counts.MEDIUM} MED</span>}
                  {counts.LOW > 0 && <span className="text-blue-400">{counts.LOW} LOW</span>}
                  {!counts.CRITICAL && !counts.HIGH && !counts.MEDIUM && !counts.LOW && (
                    <span className="text-terminal">All clear</span>
                  )}
                </div>
              )}
            </div>

            <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-5">
              {!alertsData ? (
                <div className="py-8 flex items-center justify-center gap-2 text-zinc-600 font-mono text-sm">
                  <Activity className="w-4 h-4 animate-spin" /> Querying Loki...
                </div>
              ) : !alertsData.available ? (
                <OfflineBanner reason={alertsData.reason} />
              ) : (
                <AlertFeed alerts={alertsData.alerts} />
              )}
            </div>
          </div>

          {/* Quarantine */}
          <div>
            <h3 className="text-sm font-mono text-zinc-400 flex items-center gap-2 mb-4">
              <Shield className="w-4 h-4 text-terminal" strokeWidth={1.5} />
              Quarantined Pods
            </h3>

            <div className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-5">
              {!quarantineData ? (
                <div className="py-8 flex items-center justify-center gap-2 text-zinc-600 font-mono text-sm">
                  <Activity className="w-4 h-4 animate-spin" /> Checking cluster...
                </div>
              ) : !quarantineData.available ? (
                <OfflineBanner reason={quarantineData.reason} />
              ) : quarantineData.pods?.length === 0 ? (
                <div className="py-8 text-center">
                  <div className="w-10 h-10 rounded-full bg-terminal/10 border border-terminal/20 flex items-center justify-center mx-auto mb-3">
                    <Shield className="w-5 h-5 text-terminal" strokeWidth={1.5} />
                  </div>
                  <p className="font-mono text-sm text-terminal/70">No pods quarantined</p>
                  <p className="font-mono text-xs text-zinc-600 mt-1">Self-healing policy active</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {quarantineData.pods.map(pod => (
                    <div key={pod.name} className="border border-red-500/20 bg-red-500/5 rounded-lg p-4">
                      <div className="flex items-start justify-between">
                        <div>
                          <p className="text-sm font-mono text-red-300 font-bold">{pod.name}</p>
                          <p className="text-xs font-mono text-zinc-600 mt-1">{pod.namespace}</p>
                        </div>
                        <span className="text-xs font-mono text-red-400 bg-red-500/10 px-2 py-0.5 rounded border border-red-500/20">
                          ISOLATED
                        </span>
                      </div>
                      {quarantineData.policies?.find(p => p.name.includes(pod.name.slice(-6))) && (
                        <p className="text-xs font-mono text-zinc-600 mt-2">
                          Policy: {quarantineData.policies.find(p => p.name.includes(pod.name.slice(-6)))?.name}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
