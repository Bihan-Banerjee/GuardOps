import { useRef, useState, useEffect, useCallback } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useApi } from '../hooks/useApi.js'
import { useInView } from '../hooks/useInView.js'
import { Search, ChevronDown, ChevronUp, Activity, ExternalLink, ChevronLeft, ChevronRight } from 'lucide-react'

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

const SEV_STYLES = {
  CRITICAL: { border: 'border-l-red-500',    badge: 'text-red-400 bg-red-500/10 border-red-500/30',    dot: 'bg-red-500' },
  HIGH:     { border: 'border-l-orange-500',  badge: 'text-orange-400 bg-orange-500/10 border-orange-500/30', dot: 'bg-orange-500' },
  MEDIUM:   { border: 'border-l-yellow-500',  badge: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30', dot: 'bg-yellow-500' },
  LOW:      { border: 'border-l-blue-500',    badge: 'text-blue-400 bg-blue-500/10 border-blue-500/30',   dot: 'bg-blue-500' },
}

const TOOL_COLORS = {
  semgrep:   'text-purple-400 bg-purple-500/10 border-purple-500/30',
  bandit:    'text-cyan-400 bg-cyan-500/10 border-cyan-500/30',
  trivy:     'text-blue-400 bg-blue-500/10 border-blue-500/30',
  sonarqube: 'text-orange-400 bg-orange-500/10 border-orange-500/30',
}

const SEVERITIES = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
const TOOLS = ['semgrep', 'bandit', 'trivy', 'sonarqube']

const PAGE_SIZE = 8

// Build a compact, windowed page list with ellipses, e.g. [1, '…', 4, 5, 6, '…', 12].
function getPageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(total - 1, current + 1)
  if (start > 2) pages.push('…')
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < total - 1) pages.push('…')
  pages.push(total)
  return pages
}

function FindingCard({ finding }) {
  const [expanded, setExpanded] = useState(false)
  const [showFix, setShowFix] = useState(false)
  const style = SEV_STYLES[finding.severity] || SEV_STYLES.LOW
  const toolStyle = TOOL_COLORS[finding.tool] || 'text-zinc-400 bg-zinc-800 border-zinc-700'

  const message = finding.message ?? ''
  const truncated = message.length > 120 && !expanded
  const displayMsg = truncated ? message.slice(0, 120) + '…' : message

  return (
    <div className={`border-l-4 ${style.border} bg-zinc-900/50 border border-l-0 border-zinc-800 rounded-r-xl p-4 hover:bg-zinc-900/80 transition-colors`}>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${style.badge}`}>
          {finding.severity}
        </span>
        <span className={`text-xs font-mono px-2 py-0.5 rounded border ${toolStyle}`}>
          {finding.tool}
        </span>
        <span className="text-xs font-mono text-zinc-500">{finding.rule_id}</span>
      </div>

      <p className="text-sm font-mono text-zinc-300 mb-3 leading-relaxed">
        {displayMsg}
        {message.length > 120 && (
          <button
            onClick={() => setExpanded(e => !e)}
            className="ml-2 text-terminal/60 hover:text-terminal text-xs inline-flex items-center gap-1"
          >
            {expanded ? <><ChevronUp className="w-3 h-3" /> less</> : <><ChevronDown className="w-3 h-3" /> more</>}
          </button>
        )}
      </p>

      <div className="flex flex-wrap items-center gap-4 text-xs font-mono">
        {finding.file_path && (
          <span className="text-zinc-600">
            {finding.file_path}{finding.line_start ? `:${finding.line_start}` : ''}
          </span>
        )}
        {finding.cve && (
          <a
            href={`https://nvd.nist.gov/vuln/detail/${finding.cve}`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-terminal/60 hover:text-terminal transition-colors"
          >
            {finding.cve}
            <ExternalLink className="w-3 h-3" />
          </a>
        )}
        {finding.fix_guidance && (
          <button
            onClick={() => setShowFix(f => !f)}
            className="text-zinc-600 hover:text-zinc-400 transition-colors"
          >
            {showFix ? '▲ hide fix' : '▼ show fix'}
          </button>
        )}
      </div>

      {showFix && finding.fix_guidance && (
        <div className="mt-3 pt-3 border-t border-zinc-800 text-xs font-mono text-zinc-400 bg-zinc-950/50 rounded p-3">
          <span className="text-terminal/60 mr-2">FIX:</span>
          {finding.fix_guidance}
        </div>
      )}
    </div>
  )
}

export default function Findings() {
  const ref = useRef(null)
  const inView = useInView(ref)
  const appeared = useRef(false)

  const [activeSev, setActiveSev] = useState(null)
  const [activeTool, setActiveTool] = useState(null)
  const [searchInput, setSearchInput] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [page, setPage] = useState(1)

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchInput.trim().toLowerCase()), 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const params = {
    // severity (threshold) and cve (exact) on the API don't match the UX here, so both
    // the severity pills and the free-text search are applied client-side (below).
    tool: activeTool || undefined,
    limit: 200,
  }

  const { data, loading } = useApi('/api/v1/findings', params, { refreshMs: 30_000 })
  const allFindings = data?.findings ?? []
  // Exact severity + free-text substring across CVE / rule / message / file / tool.
  const findings = allFindings.filter(f => {
    if (activeSev && f.severity !== activeSev) return false
    if (debouncedSearch) {
      const hay = `${f.cve ?? ''} ${f.rule_id ?? ''} ${f.message ?? ''} ${f.file_path ?? ''} ${f.tool ?? ''}`.toLowerCase()
      if (!hay.includes(debouncedSearch)) return false
    }
    return true
  })

  // ── Pagination (client-side over the fetched set) ──────────────────────────
  const totalPages = Math.max(1, Math.ceil(findings.length / PAGE_SIZE))
  // Reset to the first page whenever the filters change.
  useEffect(() => { setPage(1) }, [activeSev, activeTool, debouncedSearch])
  // Keep the page in range if the result set shrinks.
  useEffect(() => { if (page > totalPages) setPage(totalPages) }, [totalPages, page])
  const pagedFindings = findings.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
  const rangeStart = findings.length === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const rangeEnd = Math.min(page * PAGE_SIZE, findings.length)

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

  const FilterPill = ({ label, active, onClick }) => (
    <button
      onClick={onClick}
      className={`px-3 py-1 text-xs font-mono rounded-full border transition-all ${
        active
          ? 'border-terminal text-terminal bg-terminal/10'
          : 'border-zinc-700 text-zinc-500 hover:border-zinc-600 hover:text-zinc-300'
      }`}
    >
      {label}
    </button>
  )

  return (
    <section id="findings" className="relative bg-[#030303]/82 py-24 px-4">
      <div className="max-w-7xl mx-auto">
        <SectionHeader>FINDINGS EXPLORER</SectionHeader>

        {/* Filters */}
        <div className="flex flex-wrap gap-6 mb-8">
          {/* Severity */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-mono text-zinc-600">Severity:</span>
            <FilterPill label="ALL" active={!activeSev} onClick={() => setActiveSev(null)} />
            {SEVERITIES.map(s => (
              <FilterPill key={s} label={s} active={activeSev === s} onClick={() => setActiveSev(s === activeSev ? null : s)} />
            ))}
          </div>

          {/* Tool */}
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs font-mono text-zinc-600">Tool:</span>
            <FilterPill label="ALL" active={!activeTool} onClick={() => setActiveTool(null)} />
            {TOOLS.map(t => (
              <FilterPill key={t} label={t} active={activeTool === t} onClick={() => setActiveTool(t === activeTool ? null : t)} />
            ))}
          </div>

          {/* CVE search */}
          <div className="flex items-center gap-2">
            <Search className="w-3 h-3 text-zinc-600" />
            <input
              type="text"
              placeholder="Search CVE, rule, message, file…"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              className="bg-zinc-900 border border-zinc-700 rounded px-3 py-1 text-xs font-mono text-zinc-300 placeholder-zinc-600 focus:outline-none focus:border-terminal/50 w-56"
            />
          </div>
        </div>

        {/* Results count */}
        <div className="mb-4 text-xs font-mono text-zinc-300">
          {loading ? (
            <span className="flex items-center gap-2"><Activity className="w-3 h-3 animate-spin" /> Searching...</span>
          ) : (
            <span>
              {findings.length} finding{findings.length !== 1 ? 's' : ''} found
              {findings.length > PAGE_SIZE && (
                <span className="text-zinc-600"> · showing {rangeStart}–{rangeEnd}</span>
              )}
            </span>
          )}
        </div>

        {/* Cards */}
        <div ref={ref} className="space-y-3">
          {findings.length === 0 && !loading && (
            <div className="py-16 text-center text-zinc-300 font-mono text-sm border border-zinc-800 rounded-xl">
              No findings match the current filters.
            </div>
          )}
          {pagedFindings.map(f => (
            <FindingCard key={f.id} finding={f} />
          ))}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-8 flex items-center justify-center gap-2 font-mono text-xs">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-3 py-1 rounded-full border border-zinc-700 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-all inline-flex items-center gap-1"
              aria-label="Previous page"
            >
              <ChevronLeft className="w-3 h-3" /> Prev
            </button>

            {getPageNumbers(page, totalPages).map((p, i) =>
              p === '…' ? (
                <span key={`gap-${i}`} className="px-2 text-zinc-600 select-none">…</span>
              ) : (
                <button
                  key={p}
                  onClick={() => setPage(p)}
                  aria-current={p === page ? 'page' : undefined}
                  className={`min-w-[2rem] px-2 py-1 rounded-full border transition-all ${
                    p === page
                      ? 'border-terminal text-terminal bg-terminal/10'
                      : 'border-zinc-700 text-zinc-500 hover:border-zinc-600 hover:text-zinc-300'
                  }`}
                >
                  {p}
                </button>
              )
            )}

            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-3 py-1 rounded-full border border-zinc-700 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200 disabled:opacity-30 disabled:cursor-not-allowed transition-all inline-flex items-center gap-1"
              aria-label="Next page"
            >
              Next <ChevronRight className="w-3 h-3" />
            </button>
          </div>
        )}
      </div>
    </section>
  )
}
