import { useEffect, useState } from 'react'
import { Shield, Activity, Wifi, WifiOff } from 'lucide-react'
import { fetchMeta } from '../api.js'

const NAV_LINKS = [
  { label: 'Overview', id: 'overview' },
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
  const [connected, setConnected] = useState(null) // null=checking, true=ok, false=offline
  const [version, setVersion] = useState('v0.13.0')
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 60)
    window.addEventListener('scroll', handleScroll, { passive: true })
    return () => window.removeEventListener('scroll', handleScroll)
  }, [])

  useEffect(() => {
    fetchMeta()
      .then(data => {
        setConnected(true)
        if (data?.version) setVersion('v' + data.version)
      })
      .catch(() => setConnected(false))
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
          <div className="flex items-center gap-3">
            <Shield className="w-6 h-6 text-terminal" strokeWidth={1.5} />
            <span className="font-mono font-black text-lg tracking-widest glitch-text">
              <span className="text-terminal">GUARD</span>
              <span className="text-zinc-200">OPS</span>
            </span>
            <span className="hidden sm:inline-block text-xs text-zinc-600 font-mono border border-zinc-800 px-2 py-0.5 rounded">
              {version}
            </span>
          </div>

          {/* Desktop nav */}
          <div className="hidden md:flex items-center gap-1">
            {NAV_LINKS.map(link => (
              <button
                key={link.id}
                onClick={() => scrollTo(link.id)}
                className="px-3 py-1.5 text-xs font-mono text-zinc-400 hover:text-terminal transition-colors rounded hover:bg-terminal/5"
              >
                {link.label}
              </button>
            ))}
          </div>

          {/* Status + mobile menu */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              {connected === null && (
                <Activity className="w-3 h-3 text-zinc-500 animate-pulse" />
              )}
              {connected === true && (
                <>
                  <span className="w-2 h-2 rounded-full bg-terminal animate-pulse-slow" />
                  <span className="hidden sm:inline text-xs font-mono text-terminal/70">CONNECTED</span>
                </>
              )}
              {connected === false && (
                <>
                  <span className="w-2 h-2 rounded-full bg-red-500" />
                  <span className="hidden sm:inline text-xs font-mono text-red-500/70">OFFLINE</span>
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
                className="block w-full text-left px-3 py-2 text-sm font-mono text-zinc-400 hover:text-terminal hover:bg-terminal/5 rounded"
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
