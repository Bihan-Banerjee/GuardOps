import { useEffect, useState } from 'react'
import { Activity } from 'lucide-react'
import { fetchMeta } from '../api.js'
import { useOffline } from '../hooks/useOffline.js'
import Logo from './Logo.jsx'

const NAV_LINKS = [
  { label: 'Trends', id: 'trends' },
  { label: 'Runs', id: 'runs' },
  { label: 'Findings', id: 'findings' },
  { label: 'Runtime', id: 'runtime' },
  { label: 'Metrics', id: 'metrics' },
]

function scrollTo(id) {
  const el = document.getElementById(id)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function Navbar() {
  const [scrolled, setScrolled] = useState(false)
  const [loaded, setLoaded] = useState(false) // first meta fetch resolved (live or snapshot)
  const [version, setVersion] = useState('v1.0.0')
  const [menuOpen, setMenuOpen] = useState(false)
  const { offline } = useOffline()

  // Derived badge state: null=checking, 'live'=live API, 'snapshot'=cached fallback.
  const status = !loaded ? null : offline ? 'snapshot' : 'live'

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 60)
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    // apiFetch now resolves from the snapshot when the live API is down, so this
    // only rejects when there's no snapshot either — either way the badge state
    // comes from the shared offline signal, not from catch().
    fetchMeta()
      .then(data => {
        if (data?.version) setVersion('v' + data.version)
      })
      .finally(() => setLoaded(true))
  }, [])

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled
          ? 'bg-black/80 backdrop-blur-md border-b border-zinc-800/60'
          : 'bg-transparent'
      }`}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          {/* Logo */}
          <button
            type="button"
            onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
            className="flex items-center gap-3"
            aria-label="Scroll to top"
          >
            <Logo className="w-8 h-8 rounded-md" />
            <span className="font-mono font-black text-lg tracking-widest">
              <span className="text-terminal">GUARD</span>
              <span className="text-zinc-200">OPS</span>
            </span>
            <span className="hidden sm:inline-block text-xs text-zinc-600 font-mono border border-zinc-800 px-2 py-0.5 rounded">
              {version}
            </span>
          </button>

          {/* Desktop nav */}
          <div className="hidden md:flex items-center gap-0.5">
            {NAV_LINKS.map(link => (
              <button
                key={link.id}
                onClick={() => scrollTo(link.id)}
                className="px-4 py-2 text-sm font-mono font-medium text-zinc-200 hover:text-terminal transition-all duration-150 rounded relative group"
              >
                {link.label}
                {/* animated underline on hover */}
                <span className="absolute bottom-1 left-4 right-4 h-px bg-terminal scale-x-0 group-hover:scale-x-100 transition-transform duration-200 origin-left" />
              </button>
            ))}
          </div>

          {/* Status + mobile menu */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              {status === null && (
                <Activity className="w-3 h-3 text-zinc-500 animate-pulse" />
              )}
              {status === 'live' && (
                <>
                  <span className="w-2 h-2 rounded-full bg-terminal animate-pulse-slow" />
                  <span className="hidden sm:inline text-xs font-mono text-terminal/70">CONNECTED</span>
                </>
              )}
              {status === 'snapshot' && (
                <>
                  <span className="w-2 h-2 rounded-full bg-amber-500" />
                  <span className="hidden sm:inline text-xs font-mono text-amber-500/80">SNAPSHOT</span>
                </>
              )}
            </div>

            {/* Mobile menu button */}
            <button
              className="md:hidden p-2 text-zinc-400 hover:text-terminal"
              onClick={() => setMenuOpen(o => !o)}
            >
              <div className="space-y-1">
                <span className={`block w-5 h-0.5 bg-current transition-transform ${menuOpen ? 'rotate-45 translate-y-1.5' : ''}`} />
                <span className={`block w-5 h-0.5 bg-current transition-opacity ${menuOpen ? 'opacity-0' : ''}`} />
                <span className={`block w-5 h-0.5 bg-current transition-transform ${menuOpen ? '-rotate-45 -translate-y-1.5' : ''}`} />
              </div>
            </button>
          </div>
        </div>

        {/* Mobile menu */}
        {menuOpen && (
          <div className="md:hidden border-t border-zinc-800 py-3 space-y-1">
            {NAV_LINKS.map(link => (
              <button
                key={link.id}
                onClick={() => { scrollTo(link.id); setMenuOpen(false) }}
                className="block w-full text-left px-3 py-2 text-sm font-mono font-medium text-zinc-200 hover:text-terminal hover:bg-terminal/5 rounded"
              >
                {'>'} {link.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </nav>
  )
}
