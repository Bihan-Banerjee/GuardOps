import { useRef, useState, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { Activity } from 'lucide-react'
import {
  ResponsiveContainer, AreaChart, Area, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend,
} from 'recharts'

const PERIODS = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

const GRADIENTS = [
  { key: 'crit',   name: 'CRITICAL', color: '#ef4444', gradId: 'gradCrit'   },
  { key: 'high',   name: 'HIGH',     color: '#f97316', gradId: 'gradHigh'   },
  { key: 'medium', name: 'MEDIUM',   color: '#eab308', gradId: 'gradMedium' },
  { key: 'low',    name: 'LOW',      color: '#3b82f6', gradId: 'gradLow'    },
]

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
            {ch === ' ' ? ' ' : ch}
          </span>
        ))}
      </div>
      <div className="flex-1 h-px bg-gradient-to-r from-terminal/30 to-transparent" />
    </div>
  )
}

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  return (
    <div className="bg-zinc-950 border border-zinc-700 rounded-lg p-3 font-mono text-xs">
      <p className="text-zinc-400 mb-2">{label}</p>
      {payload.map(p => (
        <div key={p.dataKey} className="flex items-center justify-between gap-4">
          <span style={{ color: p.color }}>{p.name}</span>
          <span className="text-zinc-200 font-bold">{p.value}</span>
        </div>
      ))}
    </div>
  )
}

export default function Trends() {
  const [days, setDays] = useState(30)
  const [chartKey, setChartKey] = useState(0)
  const ref = useRef(null)
  const inView = useInView(ref)
  const appeared = useRef(false)

  const { data, loading } = useApi('/api/v1/trends', { days }, { refreshMs: 120_000 })

  const points = data?.points ?? []

  useEffect(() => {
    setChartKey(k => k + 1)
  }, [days])

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

  return (
    <section id="trends" className="relative bg-[#030303]/82 py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>SECURITY TRENDS</SectionHeader>

        {/* Period selector */}
        <div className="flex items-center gap-2 mb-8">
          <span className="text-xs font-mono text-zinc-600 mr-2">Period:</span>
          {PERIODS.map(p => (
            <button
              key={p.days}
              onClick={() => setDays(p.days)}
              className={`px-4 py-1.5 text-xs font-mono rounded-full border transition-all ${
                days === p.days
                  ? 'border-terminal text-terminal bg-terminal/10'
                  : 'border-zinc-700 text-zinc-500 hover:border-zinc-600 hover:text-zinc-400'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        <div
          ref={ref}
          className="bg-zinc-900/40 border border-zinc-800 rounded-xl p-6"
        >
          {loading && points.length === 0 ? (
            <div className="flex items-center justify-center h-[300px] gap-3 text-zinc-300 font-mono text-sm">
              <Activity className="w-4 h-4 animate-spin" />
              Loading trends...
            </div>
          ) : points.length === 0 ? (
            <div className="flex items-center justify-center h-[300px] text-zinc-300 font-mono text-sm">
              No scan data for this period
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={320}>
              <AreaChart key={chartKey} data={points} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
                <defs>
                  {GRADIENTS.map(g => (
                    <linearGradient key={g.gradId} id={g.gradId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%"  stopColor={g.color} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={g.color} stopOpacity={0} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#18181b" />
                <XAxis
                  dataKey="date"
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                  axisLine={{ stroke: '#27272a' }}
                  tickLine={false}
                  tickFormatter={d => d?.slice(5) ?? d}
                />
                <YAxis
                  tick={{ fill: '#52525b', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{
                    color: '#71717a',
                    fontFamily: 'JetBrains Mono, monospace',
                    fontSize: 11,
                    paddingTop: 16,
                  }}
                />
                {GRADIENTS.map(g => (
                  <Area
                    key={g.key}
                    type="monotone"
                    dataKey={g.key}
                    name={g.name}
                    stroke={g.color}
                    fill={`url(#${g.gradId})`}
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3, fill: g.color }}
                    animationDuration={1000}
                    animationEasing="ease-out"
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </section>
  )
}
