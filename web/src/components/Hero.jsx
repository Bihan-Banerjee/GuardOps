import { useEffect, useRef, useState, useCallback } from 'react'
import anime from 'animejs/lib/anime.es.js'
import Terminal from './Terminal.jsx'

// ─── Binary pixel art (5×5 grid) for OPS hover ───────────────────────────────
// High-resolution 7×9 bitmaps. '1' = bright green stroke, '0' = dim fill.
// Smaller characters at higher grid resolution give cleaner letter shapes.
const BINARY_PATTERNS = {
  O: [
    '0011100',
    '0100010',
    '1000001',
    '1000001',
    '1000001',
    '1000001',
    '1000001',
    '0100010',
    '0011100',
  ],
  P: [
    '1111100',
    '1000010',
    '1000010',
    '1000010',
    '1111100',
    '1000000',
    '1000000',
    '1000000',
    '1000000',
  ],
  S: [
    '0111110',
    '1000001',
    '1000000',
    '1000000',
    '0111110',
    '0000001',
    '0000001',
    '1000001',
    '0111110',
  ],
}

const TERMINAL_LINES = [
  { text: '$ guardops --about', color: 'green' },
  { text: '  GuardOps · DevSecOps Pipeline CLI · v0.13.0', color: 'cyan' },
  { text: '  Wraps your entire secure delivery pipeline —', color: 'white' },
  { text: '  build, scan, gate, deploy, and monitor — behind', color: 'white' },
  { text: '  a single command. Five security scanners gate', color: 'white' },
  { text: '  every release; runtime threats trigger', color: 'white' },
  { text: '  automatic self-healing.', color: 'white' },
  { text: ' ', color: 'dim' },
  { text: '$ guardops --features', color: 'green' },
  { text: '  ✓ Semgrep     SAST · code pattern analysis', color: 'green' },
  { text: '  ✓ Bandit      Python security linting', color: 'green' },
  { text: '  ✓ Trivy       CVE · IaC · secret scanning', color: 'green' },
  { text: '  ✓ OWASP ZAP   DAST · runtime web testing', color: 'green' },
  { text: '  ✓ Falco       runtime threat detection', color: 'green' },
  { text: '  ✓ ArgoCD      GitOps continuous delivery', color: 'green' },
  { text: '  ✓ Kyverno     admission policy enforcement', color: 'green' },
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

      {/* Binary pixel art — 7×9 grid of small chars for higher-res letters */}
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
          transform: groupHovered ? 'scale(1)' : 'scale(1.1)',
          pointerEvents: 'none',
          // 0.1em font × 9 rows ≈ 0.9em tall ≈ letter cap height
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: '0.1em',
          fontWeight: 700,
          lineHeight: 1.05,
        }}
      >
        {pattern.map((row, r) => (
          <span key={r} style={{ display: 'flex', gap: '0.06em' }}>
            {row.split('').map((bit, c) => (
              <span
                key={c}
                style={{
                  color: '#00ff41',
                  opacity: bit === '1' ? 1 : 0.12,
                  textShadow: bit === '1' ? '0 0 5px rgba(0,255,65,0.9)' : 'none',
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
      style={{
        display: 'inline-flex',
        alignItems: 'baseline',
        // Tight in normal view (matches GUARD); spaced only when binary art shows
        gap: hovered ? '0.28em' : '0',
        transition: 'gap 0.25s ease',
      }}
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
      </div>

      {/* Gradient fade into next section */}
      <div
        className="absolute bottom-0 left-0 right-0 h-40 pointer-events-none"
        style={{
          background: 'linear-gradient(to bottom, transparent, rgba(3,3,3,0.85))',
          zIndex: 10,
        }}
      />
    </section>
  )
}
