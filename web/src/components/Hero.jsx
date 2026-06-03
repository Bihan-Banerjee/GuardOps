import { useEffect, useRef, useState, useCallback } from 'react'
import anime from 'animejs/lib/anime.es.js'
import Terminal from './Terminal.jsx'
import { ChevronDown } from 'lucide-react'

// ─── Binary pixel art (5×5 grid) for OPS hover ───────────────────────────────
// '1' = bright green  '0' = very dim green
const BINARY_PATTERNS = {
  O: ['01110', '10001', '10001', '10001', '01110'],
  P: ['11110', '10001', '11110', '10000', '10000'],
  S: ['01111', '10000', '01110', '00001', '11110'],
}

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

const LETTER_SIZE = 'font-black text-[13vw] sm:text-[11vw] md:text-[10vw] lg:text-[9rem] tracking-[0.03em]'

// ─── MatrixLetter (GUARD) ─────────────────────────────────────────────────────
// Renders the letter normally; periodic glitch swaps it for 0/1 with
// chromatic aberration. No binary subtitle (removed per request).
function MatrixLetter({ char, isAnimating, sizeClass }) {
  const [display, setDisplay]         = useState(char)
  const [color,   setColor]           = useState('#00ff41')
  const [shadow,  setShadow]          = useState('0 0 40px rgba(0,255,65,0.3)')
  const [offsetX, setOffsetX]         = useState(0)
  const [glitching, setGlitching]     = useState(false)
  const timerRef = useRef(null)

  const scheduleGlitch = useCallback(() => {
    timerRef.current = setTimeout(() => {
      const frames = 5 + Math.floor(Math.random() * 6)
      let f = 0
      setGlitching(true)
      const iv = setInterval(() => {
        f++
        const r = Math.random()
        if (f < frames) {
          setDisplay(r > 0.5 ? '1' : '0')
          setColor(r > 0.55 ? '#ff2244' : '#00ff41')
          setShadow('-3px 0 #ff0040, 3px 0 #00ff41, 0 0 12px rgba(255,34,68,0.6)')
          setOffsetX((Math.random() - 0.5) * 10)
        } else {
          clearInterval(iv)
          setGlitching(false)
          setDisplay(char)
          setColor('#00ff41')
          setShadow('0 0 40px rgba(0,255,65,0.3)')
          setOffsetX(0)
          scheduleGlitch()
        }
      }, 55)
    }, 1800 + Math.random() * 5000)
  }, [char]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isAnimating) return
    const t = setTimeout(() => scheduleGlitch(), Math.random() * 2000)
    return () => { clearTimeout(t); clearTimeout(timerRef.current) }
  }, [isAnimating, scheduleGlitch])

  return (
    <span className={`hero-letter inline-block opacity-0 select-none ${sizeClass}`}>
      <span
        style={{
          display: 'inline-block',
          color,
          textShadow: shadow,
          transform: glitching ? `translateX(${offsetX}px)` : 'none',
          transition: glitching ? 'none' : 'transform 0.08s ease, color 0.08s ease',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {display}
      </span>
    </span>
  )
}

// ─── OpsLetter ────────────────────────────────────────────────────────────────
// White letter (normal). On groupHovered: fades out and binary pixel art fades
// in — both live in the same bounding box via an invisible placeholder.
function OpsLetter({ char, sizeClass, groupHovered }) {
  const pattern = BINARY_PATTERNS[char]

  return (
    <span
      className={`hero-letter relative inline-block opacity-0 select-none ${sizeClass}`}
      style={{ fontVariantNumeric: 'tabular-nums' }}
    >
      {/* Invisible placeholder keeps bounding box stable during swap */}
      <span style={{ visibility: 'hidden', userSelect: 'none', pointerEvents: 'none' }}>
        {char}
      </span>

      {/* Normal white letter */}
      <span
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#e4e4e7',
          transition: 'opacity 0.22s ease, transform 0.22s ease',
          opacity: groupHovered ? 0 : 1,
          transform: groupHovered ? 'scale(0.82)' : 'scale(1)',
          pointerEvents: 'none',
        }}
      >
        {char}
      </span>

      {/* Binary pixel art (5×5 grid sized to fill the letter bounding box) */}
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          transition: 'opacity 0.22s ease, transform 0.22s ease',
          opacity: groupHovered ? 1 : 0,
          transform: groupHovered ? 'scale(1)' : 'scale(1.12)',
          pointerEvents: 'none',
          // font-size 0.2em → 5 rows × 0.2em × lineHeight 1 = 1em total ≈ letter height
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: '0.2em',
          lineHeight: 1,
        }}
      >
        {pattern.map((row, r) => (
          <span key={r} style={{ display: 'flex', gap: '0.04em' }}>
            {row.split('').map((bit, c) => (
              <span
                key={c}
                style={{
                  color: '#00ff41',
                  opacity: bit === '1' ? 1 : 0.1,
                  textShadow: bit === '1' ? '0 0 6px rgba(0,255,65,0.8)' : 'none',
                  transition: 'opacity 0.12s',
                }}
              >
                {bit}
              </span>
            ))}
          </span>
        ))}
      </span>
    </span>
  )
}

// ─── OpsGroup ─────────────────────────────────────────────────────────────────
// Wraps O, P, S — any hover within the group triggers all three to swap.
function OpsGroup({ sizeClass }) {
  const [hovered, setHovered] = useState(false)
  return (
    <span
      style={{ display: 'inline-flex', alignItems: 'baseline', gap: '0.08em' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {['O', 'P', 'S'].map((ch, i) => (
        <OpsLetter key={i} char={ch} sizeClass={sizeClass} groupHovered={hovered} />
      ))}
    </span>
  )
}

// ─── Hero section ─────────────────────────────────────────────────────────────
function scrollToNext() {
  document.getElementById('overview')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function Hero() {
  const terminalRef  = useRef(null)
  const hasAnimated  = useRef(false)
  const [lettersReady, setLettersReady] = useState(false)

  useEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    const tl = anime.timeline({ autoplay: true })

    tl.add({
      targets: '.hero-letter',
      translateY: ['-50px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutExpo',
      duration: 700,
      delay: anime.stagger(45),
      complete: () => setLettersReady(true),
    }, 300)

    tl.add({
      targets: '.hero-subtitle',
      opacity: [0, 1],
      translateY: ['12px', '0px'],
      easing: 'easeOutCubic',
      duration: 500,
    }, 950)

    tl.add({
      targets: '.hero-terminal',
      translateY: ['30px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutBack',
      duration: 600,
    }, 1800)

    tl.add({
      targets: {},
      duration: 1,
      complete: () => terminalRef.current?.startTyping(),
    }, 2300)

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
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 65% 55% at 50% 42%, rgba(0,255,65,0.08) 0%, transparent 70%)',
        }}
      />
      <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" />

      <div className="relative z-10 flex flex-col items-center text-center px-4 w-full max-w-5xl mx-auto">

        {/* Main title */}
        <h1
          aria-label="GUARDOPS"
          className="flex items-baseline justify-center flex-wrap gap-0"
        >
          {'GUARD'.split('').map((ch, i) => (
            <MatrixLetter
              key={ch + i}
              char={ch}
              sizeClass={LETTER_SIZE}
              isAnimating={lettersReady}
            />
          ))}
          <OpsGroup sizeClass={LETTER_SIZE} />
        </h1>

        {/* Subtitle */}
        <p className="hero-subtitle opacity-0 mt-3 text-base sm:text-lg font-mono text-zinc-400 tracking-widest uppercase">
          Secure · Scan · Deploy · Monitor
        </p>

        {/* Terminal */}
        <div className="hero-terminal opacity-0 mt-10 w-full max-w-2xl">
          <Terminal ref={terminalRef} lines={TERMINAL_LINES} />
        </div>

        {/* Tool pills */}
        <div className="hero-subtitle opacity-0 mt-8 flex flex-wrap justify-center gap-2">
          {['Semgrep', 'Bandit', 'Trivy', 'OWASP ZAP', 'Falco', 'ArgoCD', 'Kyverno'].map(tool => (
            <span
              key={tool}
              className="text-xs font-mono border border-zinc-800 bg-zinc-900/40 text-zinc-500 px-3 py-1 rounded-full hover:border-terminal/40 hover:text-terminal/70 transition-colors"
            >
              {tool}
            </span>
          ))}
        </div>
      </div>

      {/* Gradient fade into next section */}
      <div
        className="absolute bottom-0 left-0 right-0 h-40 pointer-events-none"
        style={{
          background: 'linear-gradient(to bottom, transparent, rgba(3,3,3,0.85))',
          zIndex: 10,
        }}
      />

      {/* Scroll indicator */}
      <button
        className="hero-scroll opacity-0 absolute bottom-8 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center gap-2 text-zinc-600 hover:text-terminal transition-colors"
        onClick={scrollToNext}
      >
        <span className="text-xs font-mono tracking-widest uppercase">Scroll</span>
        <ChevronDown className="w-4 h-4 animate-bounce" />
      </button>
    </section>
  )
}
