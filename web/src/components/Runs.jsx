import { useRef, useEffect, useState } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { fetchRunDetail } from '../api.js'
import { ChevronDown, ChevronRight, Activity } from 'lucide-react'
import { Paginator } from './Paginator.jsx'

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

const ENV_COLORS = {
  prod:    'text-red-400 border-red-500/30 bg-red-500/10',
  staging: 'text-yellow-400 border-yellow-500/30 bg-yellow-500/10',
  local:   'text-zinc-400 border-zinc-700 bg-zinc-800/50',
}

const SEV_COLORS = {
  crit:   'text-red-400',
  high:   'text-orange-400',
  medium: 'text-yellow-400',
  low:    'text-blue-400',
}

function FindingRow({ finding }) {
  const sevColor = {
    CRITICAL: 'text-red-400 bg-red-500/10 border-red-500/30',
    HIGH:     'text-orange-400 bg-orange-500/10 border-orange-500/30',
    MEDIUM:   'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
    LOW:      'text-blue-400 bg-blue-500/10 border-blue-500/30',
  }[finding.severity] || 'text-zinc-400'

  return (
    <tr className="border-b border-zinc-800/50 hover:bg-zinc-800/20 transition-colors">
      <td className="pl-8 pr-2 py-2">
        <span className={`text-xs font-mono px-1.5 py-0.5 rounded border ${sevColor}`}>
          {finding.severity}
        </span>
      </td>
      <td className="px-2 py-2 text-xs font-mono text-zinc-500">{finding.tool}</td>
      <td className="px-2 py-2 text-xs font-mono text-zinc-400 max-w-xs truncate">{finding.rule_id}</td>
      <td className="px-2 py-2 text-xs font-mono text-zinc-300 max-w-sm truncate">{finding.message}</td>
      <td className="px-2 py-2 text-xs font-mono text-zinc-600 whitespace-nowrap">
        {finding.file_path ? `${finding.file_path}:${finding.line_start ?? ''}` : '—'}
      </td>
      {finding.cve ? (
        <td className="pl-2 pr-3 py-2">
          <a
            href={`https://nvd.nist.gov/vuln/detail/${finding.cve}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs font-mono text-terminal/60 hover:text-terminal underline"
          >
            {finding.cve}
          </a>
        </td>
      ) : <td />}
    </tr>
  )
}

function RunRow({ run }) {
  const [expanded, setExpanded] = useState(false)
  const [findings, setFindings] = useState(null)
  const [loadingFindings, setLoadingFindings] = useState(false)

  const toggle = async () => {
    setExpanded(e => !e)
    if (!findings && !loadingFindings) {
      setLoadingFindings(true)
      try {
        const detail = await fetchRunDetail(run.id)
        setFindings(detail.findings ?? [])
      } catch {
        setFindings([])
      } finally {
        setLoadingFindings(false)
      }
    }
  }

  const envClass = ENV_COLORS[run.environment] || ENV_COLORS.local

  return (
    <>
      <tr
        className={`border-b border-zinc-800/50 hover:bg-zinc-800/30 cursor-pointer transition-colors
          ${expanded ? 'bg-zinc-800/20' : ''}`}
        onClick={toggle}
      >
        <td className="pl-4 pr-2 py-3 text-xs font-mono text-zinc-600">#{run.id}</td>
        <td className="px-2 py-3 text-xs font-mono text-zinc-300">{run.project_name}</td>
        <td className="px-2 py-3 text-xs font-mono text-zinc-500 max-w-[140px] truncate hidden sm:table-cell">
          {run.image_ref?.split('/').pop() ?? run.image_ref ?? '—'}
        </td>
        <td className="px-2 py-3">
          <span className={`text-xs font-mono px-2 py-0.5 rounded border ${envClass}`}>
            {run.environment ?? '—'}
          </span>
        </td>
        <td className={`px-2 py-3 text-xs font-mono font-bold ${run.crit_count > 0 ? SEV_COLORS.crit : 'text-zinc-700'}`}>
          {run.crit_count}
        </td>
        <td className={`px-2 py-3 text-xs font-mono font-bold ${run.high_count > 0 ? SEV_COLORS.high : 'text-zinc-700'}`}>
          {run.high_count}
        </td>
        <td className={`px-2 py-3 text-xs font-mono font-bold hidden md:table-cell ${run.medium_count > 0 ? SEV_COLORS.medium : 'text-zinc-700'}`}>
          {run.medium_count}
        </td>
        <td className={`px-2 py-3 text-xs font-mono font-bold hidden md:table-cell ${run.low_count > 0 ? SEV_COLORS.low : 'text-zinc-700'}`}>
          {run.low_count}
        </td>
        <td className="px-2 py-3">
          <span className={`text-xs font-mono px-2 py-0.5 rounded font-bold ${
            run.blocked
              ? 'text-red-400 bg-red-500/10'
              : 'text-terminal bg-terminal/10'
          }`}>
            {run.blocked ? 'BLOCKED' : 'PASSED'}
          </span>
        </td>
        <td className="pr-4 py-3 text-zinc-600">
          {expanded
            ? <ChevronDown className="w-3 h-3" />
            : <ChevronRight className="w-3 h-3" />}
        </td>
      </tr>

      {/* Expanded findings */}
      {expanded && (
        <tr>
          <td colSpan={10} className="bg-zinc-950/50">
            {loadingFindings ? (
              <div className="py-4 pl-8 text-xs font-mono text-zinc-300 flex items-center gap-2">
                <Activity className="w-3 h-3 animate-spin" /> Loading findings...
              </div>
            ) : findings?.length === 0 ? (
              <div className="py-4 pl-8 text-xs font-mono text-zinc-300">No findings for this run.</div>
            ) : (
              <table className="w-full">
                <thead>
                  <tr className="text-zinc-600 text-xs font-mono">
                    <th className="pl-8 pr-2 py-2 text-left">SEV</th>
                    <th className="px-2 py-2 text-left">TOOL</th>
                    <th className="px-2 py-2 text-left">RULE</th>
                    <th className="px-2 py-2 text-left">MESSAGE</th>
                    <th className="px-2 py-2 text-left">FILE</th>
                    <th className="pl-2 pr-3 py-2 text-left">CVE</th>
                  </tr>
                </thead>
                <tbody>
                  {findings?.map(f => <FindingRow key={f.id} finding={f} />)}
                </tbody>
              </table>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

const PAGE_SIZE = 10

export default function Runs() {
  const ref = useRef(null)
  const inView = useInView(ref)
  const appeared = useRef(false)
  const [page, setPage] = useState(1)

  // Fetch up to 50 runs; paginate client-side so the table never gets unwieldy.
  const { data, loading } = useApi('/api/v1/runs', { limit: 50 }, { refreshMs: 30_000 })
  const runs = data?.runs ?? []

  const totalPages = Math.max(1, Math.ceil(runs.length / PAGE_SIZE))
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [totalPages, page])
  const pagedRuns  = runs.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const rangeStart = runs.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd   = Math.min(page * PAGE_SIZE, runs.length)

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
    <section id="runs" className="relative bg-[#030303]/82 py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>SCAN RUNS</SectionHeader>

        {/* Result count */}
        <div className="mb-3 text-xs font-mono text-zinc-600">
          {loading ? (
            <span className="flex items-center gap-2">
              <Activity className="w-3 h-3 animate-spin" /> Loading…
            </span>
          ) : runs.length > 0 ? (
            <span>
              {runs.length} run{runs.length !== 1 ? 's' : ''}
              {runs.length > PAGE_SIZE && (
                <span className="text-zinc-700"> · showing {rangeStart}–{rangeEnd}</span>
              )}
            </span>
          ) : null}
        </div>

        <div ref={ref} className="rounded-xl border border-zinc-800 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-zinc-900/80 border-b border-zinc-800">
                <tr className="text-zinc-500 text-xs font-mono uppercase tracking-wider">
                  <th className="pl-4 pr-2 py-3 text-left">#</th>
                  <th className="px-2 py-3 text-left">Project</th>
                  <th className="px-2 py-3 text-left hidden sm:table-cell">Image</th>
                  <th className="px-2 py-3 text-left">Env</th>
                  <th className="px-2 py-3 text-left text-red-400/70">CRIT</th>
                  <th className="px-2 py-3 text-left text-orange-400/70">HIGH</th>
                  <th className="px-2 py-3 text-left hidden md:table-cell text-yellow-400/70">MED</th>
                  <th className="px-2 py-3 text-left hidden md:table-cell text-blue-400/70">LOW</th>
                  <th className="px-2 py-3 text-left">Status</th>
                  <th className="pr-4 py-3" />
                </tr>
              </thead>
              <tbody>
                {pagedRuns.map(run => (
                  <RunRow key={run.id} run={run} />
                ))}
              </tbody>
            </table>
          </div>

          {loading && runs.length === 0 && (
            <div className="py-16 text-center text-zinc-300 font-mono text-sm flex items-center justify-center gap-2">
              <Activity className="w-4 h-4 animate-spin" /> Loading scan history...
            </div>
          )}
          {!loading && runs.length === 0 && (
            <div className="py-16 text-center text-zinc-300 font-mono text-sm">
              No scan runs found. Run{' '}
              <code className="text-terminal/70">guardops deploy</code> to create your first scan.
            </div>
          )}
        </div>

        <Paginator
          page={page}
          totalPages={totalPages}
          setPage={setPage}
          className="mt-6"
        />
      </div>
    </section>
  )
}
