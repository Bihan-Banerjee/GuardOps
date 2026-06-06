import { ChevronLeft, ChevronRight } from 'lucide-react'

/**
 * Build a compact, windowed page list with ellipses.
 * e.g. [1, '…', 4, 5, 6, '…', 12]
 */
export function getPageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = [1]
  const start = Math.max(2, current - 1)
  const end   = Math.min(total - 1, current + 1)
  if (start > 2) pages.push('…')
  for (let i = start; i <= end; i++) pages.push(i)
  if (end < total - 1) pages.push('…')
  pages.push(total)
  return pages
}

/**
 * Shared pagination bar used by Runs, Runtime, and Findings.
 * Renders nothing when totalPages <= 1.
 *
 * Props:
 *   page       – current page (1-indexed)
 *   totalPages – total number of pages
 *   setPage    – setState setter
 *   className  – extra classes on the container (e.g. spacing / border)
 */
export function Paginator({ page, totalPages, setPage, className = '' }) {
  if (totalPages <= 1) return null

  return (
    <div className={`flex items-center justify-center gap-2 font-mono text-xs ${className}`}>
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
  )
}
