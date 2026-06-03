import { useRef, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { Activity } from 'lucide-react'

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
      <div aria-hidden="true" className="flex gap-0.5 whitespace-pre">
        {children.split('').map((ch, i) => (
          <span
            key={i}
            className="sh-letter inline-block font-black text-2xl sm:text-3xl font-mono tracking-[0.15em] opacity-0"
            style={{ color: '#e4e4e7' }}
          >
            {ch === ' ' ? ' ' : ch}
          </span>
        ))}
      </div>
      <div className="flex-1 h-px bg-gradient-to-r from-terminal/30 to-transparent" />
    </div>
  )
}

export default function Overview() {
  const { data, loading } = useApi('/api/v1/summary', {}, { refreshMs: 60_000 })

  const summary      = data || {}
  const byTool       = summary.by_tool || []
  const latest       = summary.latest
  const maxToolCount = Math.max(...byTool.map(t => t.count), 1)

  const toolBarRef    = useRef(null)
  const inView        = useInView(toolBarRef)
  const barsAnimated  = useRef(false)

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

        {/* Brief tool description */}
        <p className="max-w-3xl text-sm sm:text-base leading-relaxed text-zinc-300 mb-12">
          <span className="text-terminal font-semibold">GuardOps</span> is a DevSecOps CLI
          that wraps your entire secure delivery pipeline — build, scan, gate, deploy, and
          monitor — behind a single command. Five security scanners gate every release, and
          runtime threats trigger automatic self-healing.
        </p>

        {/* Latest run status banner */}
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
                {latest.timestamp ? new Date(latest.timestamp).toLocaleString() : '—'}
              </span>
            </div>
          </div>
        )}

        {/* Findings by scanner — animated horizontal bars */}
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
            <div className="inline-flex items-center gap-3 text-zinc-300 font-mono text-sm">
              <Activity className="w-4 h-4 animate-spin" />
              Loading scan data...
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
