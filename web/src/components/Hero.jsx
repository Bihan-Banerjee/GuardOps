import { useEffect, useRef } from 'react'
import anime from 'animejs/lib/anime.es.js'
import Terminal from './Terminal.jsx'
import { ChevronDown } from 'lucide-react'

const TITLE_LETTERS = 'GUARDOPS'.split('')

const TERMINAL_LINES = [
  { text: '$ guardops deploy --env prod --gitops --fail-on HIGH', color: 'green' },
  { text: '  [1/5] Building Docker image...', color: 'dim' },
  { text: '  [2/5] Running security scanners...', color: 'dim' },
  { text: '    ✓ Semgrep   — 0 CRITICAL, 2 HIGH', color: 'yellow' },
  { text: '    ✓ Bandit    — 0 CRITICAL, 1 HIGH', color: 'yellow' },
  { text: '    ✓ Trivy-fs  — 0 secrets detected', color: 'green' },
  { text: '    ✓ Trivy-img — 3 CVEs (fixable)', color: 'yellow' },
  { text: '  [3/5] Security gate: PASSED ✓', color: 'green' },
  { text: '  [4/5] Pushing to ECR...', color: 'dim' },
  { text: '  [5/5] Helm upgrade → prod namespace', color: 'dim' },
  { text: '  ✓ Deploy complete. Revision: 4a8f2c1', color: 'green' },
  { text: '$ guardops diff --from 41 --to 42', color: 'green' },
  { text: '  No new CRITICAL/HIGH findings. Gate: PASS', color: 'green' },
]

function scrollToNext() {
  const el = document.getElementById('overview')
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function Hero() {
  const terminalRef = useRef(null)
  const hasAnimated = useRef(false)

  useEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    const tl = anime.timeline({ autoplay: true })

    // Letter stagger
    tl.add({
      targets: '.hero-letter',
      translateY: ['-50px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutExpo',
      duration: 700,
      delay: anime.stagger(45),
    }, 300)

    // Subtitle reveal
    tl.add({
      targets: '.hero-subtitle',
      opacity: [0, 1],
      translateY: ['12px', '0px'],
      easing: 'easeOutCubic',
      duration: 500,
    }, 900)

    // Tag line
    tl.add({
      targets: '.hero-tagline',
      opacity: [0, 1],
      translateY: ['8px', '0px'],
      easing: 'easeOutCubic',
      duration: 400,
    }, 1150)

    // Terminal window
    tl.add({
      targets: '.hero-terminal',
      translateY: ['30px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutBack',
      duration: 600,
    }, 1800)

    // Start terminal typing
    tl.add({
      targets: {},
      duration: 1,
      complete: () => terminalRef.current?.startTyping(),
    }, 2300)

    // Scroll indicator
    tl.add({
      targets: '.hero-scroll',
      opacity: [0, 1],
      duration: 600,
      easing: 'easeInOutSine',
    }, 3000)
  }, [])

  return (
    <section
      id="hero"
      className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden scanlines"
    >
      {/* Radial glow behind text */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 60% 50% at 50% 45%, rgba(0,255,65,0.07) 0%, transparent 70%)',
        }}
      />

      {/* Grid background */}
      <div className="absolute inset-0 grid-bg opacity-40 pointer-events-none" />

      {/* Main content */}
      <div className="relative z-10 flex flex-col items-center text-center px-4 w-full max-w-5xl mx-auto">

        {/* Pre-title badge */}
        <div className="hero-tagline opacity-0 mb-6">
          <span className="inline-flex items-center gap-2 border border-terminal/30 bg-terminal/5 rounded-full px-4 py-1.5 text-xs font-mono text-terminal/80">
            <span className="w-1.5 h-1.5 rounded-full bg-terminal animate-pulse" />
            DevSecOps Pipeline CLI · v0.13.0
          </span>
        </div>

        {/* Main title */}
        <h1
          aria-label="GUARDOPS"
          className="flex items-center justify-center gap-0 md:gap-1 flex-wrap"
        >
          {TITLE_LETTERS.map((ch, i) => (
            <span
              key={i}
              className="hero-letter inline-block font-black text-7xl sm:text-8xl md:text-[9rem] lg:text-[10rem] tracking-[0.05em] opacity-0 select-none"
              style={{
                color: i < 5 ? '#00ff41' : '#e4e4e7',
                textShadow: i < 5 ? '0 0 40px rgba(0,255,65,0.3)' : 'none',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {ch}
            </span>
          ))}
        </h1>

        {/* Subtitle */}
        <p className="hero-subtitle opacity-0 mt-4 text-lg sm:text-xl font-mono text-zinc-400 tracking-widest uppercase">
          Secure · Scan · Deploy · Monitor
        </p>

        {/* Terminal */}
        <div className="hero-terminal opacity-0 mt-10 w-full max-w-2xl">
          <Terminal ref={terminalRef} lines={TERMINAL_LINES} />
        </div>

        {/* Feature pills */}
        <div className="hero-subtitle opacity-0 mt-8 flex flex-wrap justify-center gap-2">
          {['Semgrep', 'Bandit', 'Trivy', 'OWASP ZAP', 'Falco', 'ArgoCD', 'Kyverno'].map(tool => (
            <span
              key={tool}
              className="text-xs font-mono border border-zinc-800 bg-zinc-900/60 text-zinc-500 px-3 py-1 rounded-full hover:border-terminal/40 hover:text-terminal/70 transition-colors"
            >
              {tool}
            </span>
          ))}
        </div>
      </div>

      {/* Bottom gradient fade */}
      <div
        className="absolute bottom-0 left-0 right-0 h-40 pointer-events-none"
        style={{
          background: 'linear-gradient(to bottom, transparent, #030303)',
          zIndex: 10,
        }}
      />

      {/* Scroll indicator */}
      <button
        className="hero-scroll opacity-0 absolute bottom-8 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center gap-2 text-zinc-600 hover:text-terminal transition-colors"
        onClick={scrollToNext}
        style={{ zIndex: 20 }}
      >
        <span className="text-xs font-mono tracking-widest uppercase">Scroll</span>
        <ChevronDown className="w-4 h-4 animate-bounce" />
      </button>
    </section>
  )
}
