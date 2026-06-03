import { useEffect, useRef, useState, useCallback } from 'react'
import anime from 'animejs/lib/anime.es.js'
import Terminal from './Terminal.jsx'
import { ChevronDown } from 'lucide-react'

// ASCII binary representations of each GUARD letter
const ASCII_BINARY = {
  G: '01000111',
  U: '01010101',
  A: '01000001',
  R: '01010010',
  D: '01000100',
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

// ─── Binary Matrix Letter ─────────────────────────────────────────────────────
// Each letter in "GUARD" shows its 8-bit ASCII encoding underneath.
// Bits randomly flip at low probability. Periodic glitch swaps the letter
// for a 0/1 character with chromatic aberration for ~350ms.
function MatrixLetter({ char, isAnimating, sizeClass }) {
  const originalBinary = ASCII_BINARY[char] || '01001101'
  const [binary, setBinary]           = useState(originalBinary)
  const [glitchDisplay, setGlitchDisplay] = useState(char)
  const [glitchColor,   setGlitchColor]   = useState('#00ff41')
  const [glitchShadow,  setGlitchShadow]  = useState('0 0 40px rgba(0,255,65,0.3)')
  const [glitchOffset,  setGlitchOffset]  = useState(0)
  const [isGlitching,   setIsGlitching]   = useState(false)
  const timerRef = useRef(null)

  // Slowly flip individual bits in the binary string
  useEffect(() => {
    if (!isAnimating) return
    const id = setInterval(() => {
      setBinary(prev => {
        if (Math.random() > 0.35) return prev          // 65% chance: no change
        const arr = prev.split('')
        const idx = Math.floor(Math.random() * arr.length)
        arr[idx] = arr[idx] === '0' ? '1' : '0'
        return arr.join('')
      })
    }, 380)
    return () => clearInterval(id)
  }, [isAnimating])

  // Drift back toward correct binary occasionally so it doesn't wander too far
  useEffect(() => {
    if (!isAnimating) return
    const id = setInterval(() => {
      if (Math.random() < 0.12) setBinary(originalBinary)
    }, 2800)
    return () => clearInterval(id)
  }, [isAnimating, originalBinary])

  // Glitch event scheduler
  const scheduleGlitch = useCallback(() => {
    const wait = 1800 + Math.random() * 5000
    timerRef.current = setTimeout(() => {
      const totalFrames = 5 + Math.floor(Math.random() * 6)  // 5-10 frames
      let frame = 0
      setIsGlitching(true)

      const interval = setInterval(() => {
        frame++
        const r = Math.random()

        if (frame < totalFrames) {
          setGlitchDisplay(r > 0.5 ? '1' : '0')
          setGlitchColor(r > 0.55 ? '#ff2244' : '#00ff41')
          setGlitchShadow('-3px 0 #ff0040, 3px 0 #00ff41, 0 0 12px rgba(255,34,68,0.6)')
          setGlitchOffset((Math.random() - 0.5) * 10)
        } else {
          clearInterval(interval)
          setIsGlitching(false)
          setGlitchDisplay(char)
          setGlitchColor('#00ff41')
          setGlitchShadow('0 0 40px rgba(0,255,65,0.3)')
          setGlitchOffset(0)
          scheduleGlitch()
        }
      }, 55)
    }, wait)
  }, [char]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!isAnimating) return
    // Stagger start so letters don't all glitch at once
    const initDelay = setTimeout(() => scheduleGlitch(), Math.random() * 2000)
    return () => {
      clearTimeout(initDelay)
      clearTimeout(timerRef.current)
    }
  }, [isAnimating, scheduleGlitch])

  return (
    <span className={`hero-letter relative inline-block opacity-0 select-none ${sizeClass}`}>
      {/* Main letter / glitch character */}
      <span
        style={{
          display: 'inline-block',
          color: glitchColor,
          textShadow: glitchShadow,
          transform: isGlitching ? `translateX(${glitchOffset}px)` : 'none',
          transition: isGlitching ? 'none' : 'transform 0.08s ease, color 0.08s ease',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {glitchDisplay}
      </span>

      {/* 8-bit ASCII binary encoding — bottom of letter, monospace, glows */}
      <span
        aria-hidden="true"
        style={{
          position: 'absolute',
          bottom: '0.08em',
          left: 0,
          right: 0,
          textAlign: 'center',
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 'clamp(6px, 0.9vw, 10px)',
          letterSpacing: '0.5px',
          color: '#00ff41',
          opacity: isGlitching ? 0.85 : 0.38,
          lineHeight: 1,
          pointerEvents: 'none',
          // Shift individual bits to mimic active glitch
          filter: isGlitching ? 'blur(0.5px)' : 'none',
          transition: 'opacity 0.15s',
        }}
      >
        {isGlitching
          ? binary.split('').map(b => Math.random() > 0.6 ? (b === '0' ? '1' : '0') : b).join('')
          : binary}
      </span>
    </span>
  )
}

// ─── Plain letter (OPS) ───────────────────────────────────────────────────────
function PlainLetter({ char, sizeClass, color = '#e4e4e7' }) {
  return (
    <span
      className={`hero-letter inline-block opacity-0 select-none ${sizeClass}`}
      style={{ color }}
    >
      {char}
    </span>
  )
}

// ─── Hero ─────────────────────────────────────────────────────────────────────
const LETTER_SIZE = 'font-black text-[13vw] sm:text-[11vw] md:text-[10vw] lg:text-[9rem] tracking-[0.03em]'

function scrollToNext() {
  document.getElementById('overview')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function Hero() {
  const terminalRef    = useRef(null)
  const hasAnimated    = useRef(false)
  const [lettersReady, setLettersReady] = useState(false)

  useEffect(() => {
    if (hasAnimated.current) return
    hasAnimated.current = true

    const tl = anime.timeline({ autoplay: true })

    // Letter stagger (all 8 letters share the .hero-letter selector)
    tl.add({
      targets: '.hero-letter',
      translateY: ['-50px', '0px'],
      opacity: [0, 1],
      easing: 'easeOutExpo',
      duration: 700,
      delay: anime.stagger(45),
      complete: () => setLettersReady(true),   // start binary glitch after reveal
    }, 300)

    // Subtitle
    tl.add({
      targets: '.hero-subtitle',
      opacity: [0, 1],
      translateY: ['12px', '0px'],
      easing: 'easeOutCubic',
      duration: 500,
    }, 950)

    // Tag badge
    tl.add({
      targets: '.hero-tagline',
      opacity: [0, 1],
      translateY: ['8px', '0px'],
      easing: 'easeOutCubic',
      duration: 400,
    }, 200)

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
      {/* Radial green glow behind title */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse 65% 55% at 50% 42%, rgba(0,255,65,0.08) 0%, transparent 70%)',
        }}
      />

      {/* Subtle grid overlay */}
      <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" />

      {/* Content */}
      <div className="relative z-10 flex flex-col items-center text-center px-4 w-full max-w-5xl mx-auto">

        {/* Badge */}
        <div className="hero-tagline opacity-0 mb-6">
          <span className="inline-flex items-center gap-2 border border-terminal/30 bg-terminal/5 rounded-full px-4 py-1.5 text-xs font-mono text-terminal/80">
            <span className="w-1.5 h-1.5 rounded-full bg-terminal animate-pulse" />
            DevSecOps Pipeline CLI · v0.13.0
          </span>
        </div>

        {/* Main title */}
        <h1
          aria-label="GUARDOPS"
          className="flex items-baseline justify-center flex-wrap gap-0"
        >
          {/* GUARD — binary matrix letters */}
          {'GUARD'.split('').map((ch, i) => (
            <MatrixLetter
              key={ch + i}
              char={ch}
              sizeClass={LETTER_SIZE}
              isAnimating={lettersReady}
            />
          ))}
          {/* OPS — plain white letters */}
          {'OPS'.split('').map((ch, i) => (
            <PlainLetter key={ch + i} char={ch} sizeClass={LETTER_SIZE} />
          ))}
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

      {/* Bottom gradient fade into next section */}
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
