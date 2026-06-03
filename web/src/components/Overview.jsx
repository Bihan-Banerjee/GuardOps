import { useRef, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { Shield, Activity, AlertTriangle, Wrench } from 'lucide-react'

function SectionHeader({ children, className = '' }) {
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
    <div ref={ref} className={`flex items-center gap-4 mb-10 ${className}`}>
      <div aria-hidden="true" className="flex gap-0.5 whitespace-pre">
        {children.split('').map((ch, i) => (
          <span
            key={i}
            className="sh-letter inline-block font-black text-2xl sm:text-3xl font-mono tracking-[0.15em] opacity-0"
            style={{ color: '#e4e4e7' }}
          >
            {ch === ' ' ? ' ' : ch}
          </span>
        ))}
      </div>
      <div className="flex-1 h-px bg-gradient-to-r from-terminal/30 to-transparent" />
    </div>
  )
}

function CounterCard({ label, value, suffix = '', icon: Icon, color = 'terminal', delay = 0 }) {
  const ref = useRef(null)
  const counterRef = useRef(null)
  const inView = useInView(ref)
  const animated = useRef(false)

  useEffect(() => {
    if (!inView || animated.current) return
    animated.current = true

    anime({
      targets: ref.current,
      translateY: ['20px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutExpo',
      duration: 600,
      delay,
    })

    const numericVal = parseFloat(value)
    if (!isNaN(numericVal) && counterRef.current) {
      const obj = { val: 0 }
      anime({
        targets: obj,
        val: numericVal,
        round: numericVal < 1 ? 0 : 1,
        duration: 1200,
        delay: delay + 200,
        easing: 'easeOutQuart',
        update: () => {
          if (counterRef.current) {
            const display = numericVal < 1
              ? Math.round(obj.val * 100) + '%'
              : Math.round(obj.val).toLocaleString() + suffix
            counterRef.current.textContent = display
          }
        }
      })
    }
  }, [inView, value, delay, suffix])

  const colorMap = {
    terminal: 'border-terminal/20 hover:border-terminal/40',
    red: 'border-red-500/20 hover:border-red-500/40',
    yellow: 'border-yellow-500/20 hover:border-yellow-500/40',
    blue: 'border-blue-500/20 hover:border-blue-500/40',
  }
  const textColorMap = {
    terminal: 'text-terminal',
    red: 'text-red-400',
    yellow: 'text-yellow-400',
    blue: 'text-blue-400',
  }

  const displayValue = () => {
    if (value === null || value === undefined) return '—'
    const n = parseFloat(value)
    if (isNaN(n)) return value
    if (n < 1 && value !== 0) return Math.round(n * 100) + '%'
    return Math.round(n).toLocaleString() + suffix
  }

  return (
    <div
      ref={ref}
      className={`opacity-0 bg-zinc-900/60 border rounded-xl p-6 backdrop-blur-sm transition-all duration-300 group ${colorMap[color]}`}
    >
      <div className="flex items-start justify-between mb-4">
        <Icon className={`w-5 h-5 ${textColorMap[color]} opacity-70`} strokeWidth={1.5} />
        <span className="text-xs font-mono text-zinc-600 uppercase tracking-wider">{label}</span>
      </div>
      <div
        ref={counterRef}
        className={`text-4xl font-black font-mono ${textColorMap[color]}`}
      >
        {displayValue()}
      </div>
    </div>
  )
}

export default function Overview() {
  const { data, loading } = useApi('/api/v1/summary', {}, { refreshMs: 60_000 })

  const summary = data || {}
  const bySeverity = summary.by_severity || {}
  const byTool = summary.by_tool || []
  const latest = summary.latest

  const maxToolCount = Math.max(...byTool.map(t => t.count), 1)

  const toolBarRef = useRef(null)
  const inView = useInView(toolBarRef)
  const barsAnimated = useRef(false)

  useEffect(() => {
    if (!inView || barsAnimated.current || byTool.length === 0) return
    barsAnimated.current = true
    anime({
      targets: '.tool-bar-fill',
      width: (el) => el.getAttribute('data-width') + '%',
      easing: 'easeOutExpo',
      duration: 800,
      delay: anime.stagger(100),
    })
  }, [inView, byTool])

  return (
    <section id="overview" className="relative bg-[#030303]/82 py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>OVERVIEW</SectionHeader>

        {/* Metric cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-12">
          <CounterCard
            label="Total Scans"
            value={summary.total_runs ?? 0}
            icon={Activity}
            color="terminal"
            delay={0}
          />
          <CounterCard
            label="Gate Pass Rate"
            value={summary.gate_pass_rate ?? 0}
            icon={Shield}
            color="terminal"
            delay={80}
          />
          <CounterCard
            label="Critical Findings"
            value={bySeverity.CRITICAL ?? 0}
            icon={AlertTriangle}
            color="red"
            delay={160}
          />
          <CounterCard
            label="Tools Active"
            value={byTool.length || 4}
            icon={Wrench}
            color="blue"
            delay={240}
          />
        </div>

        {/* Latest run banner */}
        {latest && (
          <div
            className={`mb-12 rounded-xl border p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${
              latest.blocked
                ? 'border-red-500/30 bg-red-500/5'
                : 'border-terminal/30 bg-terminal/5'
            }`}
          >
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-3">
                <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded ${
                  latest.blocked ? 'bg-red-500/20 text-red-400' : 'bg-terminal/20 text-terminal'
                }`}>
                  {latest.blocked ? 'BLOCKED' : 'PASSED'}
                </span>
                <span className="text-sm font-mono text-zinc-300">{latest.project_name}</span>
                <span className="text-xs font-mono text-zinc-600">{latest.environment}</span>
              </div>
              <span className="text-xs font-mono text-zinc-500 truncate max-w-xs">
                {latest.image_ref}
              </span>
            </div>
            <div className="flex items-center gap-6 font-mono text-xs">
              <span className="text-red-400">{latest.crit_count} CRIT</span>
              <span className="text-orange-400">{latest.high_count} HIGH</span>
              <span className="text-yellow-400">{latest.medium_count} MED</span>
              <span className="text-blue-400">{latest.low_count} LOW</span>
              <span className="text-zinc-600">
                {latest.timestamp
                  ? new Date(latest.timestamp).toLocaleString()
                  : '—'}
              </span>
            </div>
          </div>
        )}

        {/* Tool breakdown */}
        {byTool.length > 0 && (
          <div ref={toolBarRef} className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6">
            <h3 className="text-xs font-mono text-zinc-500 uppercase tracking-wider mb-6">
              Findings by Scanner
            </h3>
            <div className="space-y-4">
              {byTool.map(({ tool, count }) => (
                <div key={tool} className="flex items-center gap-4">
                  <span className="w-20 text-xs font-mono text-zinc-400 uppercase shrink-0">
                    {tool}
                  </span>
                  <div className="flex-1 bg-zinc-800 rounded-full h-2 overflow-hidden">
                    <div
                      className="tool-bar-fill h-full rounded-full bg-terminal"
                      style={{ width: 0 }}
                      data-width={Math.round((count / maxToolCount) * 100)}
                    />
                  </div>
                  <span className="w-10 text-right text-xs font-mono text-zinc-400">
                    {count}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {loading && !data && (
          <div className="text-center py-20">
            <div className="inline-flex items-center gap-3 text-zinc-600 font-mono text-sm">
              <Activity className="w-4 h-4 animate-spin" />
              Loading scan data...
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
