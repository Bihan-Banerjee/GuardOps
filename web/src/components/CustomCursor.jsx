import { useEffect, useRef } from 'react'

// Tags that should trigger the "hover" state
const HOVER_TAGS = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'SUMMARY'])

export default function CustomCursor() {
  const outerRef = useRef(null)   // position layer — updated by RAF (no transition)
  const innerRef = useRef(null)   // scale + rotate layer — CSS transition handles easing
  const pos      = useRef({ x: -300, y: -300 })
  const curr     = useRef({ x: -300, y: -300 })
  const hovered  = useRef(false)
  const rafId    = useRef(null)

  useEffect(() => {
    // Inject global cursor: none — remove on unmount
    const styleEl = document.createElement('style')
    styleEl.id = 'guardops-cursor-hide'
    styleEl.textContent = '*, *::before, *::after { cursor: none !important; }'
    document.head.appendChild(styleEl)

    // ── Hover detection ─────────────────────────────────────────────────────
    const applyHover = (val) => {
      if (hovered.current === val) return
      hovered.current = val
      const el = innerRef.current
      if (!el) return
      el.style.transform = val
        ? 'scale(1.55) rotate(-25deg)'
        : 'scale(1) rotate(0deg)'
      el.style.filter = val
        ? 'drop-shadow(0 0 10px #00ff41) drop-shadow(0 0 4px rgba(0,255,65,0.7))'
        : 'drop-shadow(0 0 5px rgba(0,255,65,0.4))'
    }

    const onOver = (e) => {
      let el = e.target
      // Walk up DOM — stop after 8 levels to avoid perf hit
      for (let i = 0; i < 8 && el && el.nodeType === 1; i++) {
        const tag  = el.tagName
        const role = el.getAttribute?.('role')
        if (
          HOVER_TAGS.has(tag) ||
          role === 'button' ||
          role === 'link' ||
          (tag !== 'HTML' && window.getComputedStyle(el).cursor === 'pointer')
        ) {
          applyHover(true)
          return
        }
        el = el.parentElement
      }
      applyHover(false)
    }

    // ── Mouse tracking ───────────────────────────────────────────────────────
    const onMove = (e) => { pos.current = { x: e.clientX, y: e.clientY } }
    const onLeave = () => { if (outerRef.current) outerRef.current.style.opacity = '0' }
    const onEnter = () => { if (outerRef.current) outerRef.current.style.opacity = '1' }

    window.addEventListener('mousemove', onMove, { passive: true })
    document.addEventListener('mouseover', onOver, { passive: true })
    document.addEventListener('mouseleave', onLeave)
    document.addEventListener('mouseenter', onEnter)

    // ── RAF position loop ───────────────────────────────────────────────────
    // Lerp factor: higher = snappier, lower = more lag / trail effect
    const LERP = 0.2
    const loop = () => {
      const el = outerRef.current
      if (el) {
        curr.current.x += (pos.current.x - curr.current.x) * LERP
        curr.current.y += (pos.current.y - curr.current.y) * LERP
        el.style.transform = `translate(${curr.current.x}px, ${curr.current.y}px)`
      }
      rafId.current = requestAnimationFrame(loop)
    }
    rafId.current = requestAnimationFrame(loop)

    return () => {
      document.getElementById('guardops-cursor-hide')?.remove()
      window.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseover', onOver)
      document.removeEventListener('mouseleave', onLeave)
      document.removeEventListener('mouseenter', onEnter)
      cancelAnimationFrame(rafId.current)
    }
  }, [])

  return (
    // outerRef: translates to cursor position via RAF (no CSS transition on this layer)
    <div
      ref={outerRef}
      className="fixed top-0 left-0 pointer-events-none z-[9999]"
      style={{
        transform: 'translate(-300px, -300px)',
        willChange: 'transform',
        transition: 'opacity 0.2s ease',
      }}
    >
      {/*
        innerRef: handles scale + tilt via CSS transition.
        transformOrigin '0 0' so it scales from the cursor hot-point
        (the lens center, which we've placed at the outer div's origin
        via a translate(-13, -13) on the SVG below).
      */}
      <div
        ref={innerRef}
        style={{
          transformOrigin: '0 0',
          transition: 'transform 0.22s cubic-bezier(0.23, 1, 0.32, 1), filter 0.22s ease',
          filter: 'drop-shadow(0 0 5px rgba(0,255,65,0.4))',
        }}
      >
        {/*
          SVG is shifted –13px, –13px so the lens center (13,13)
          sits exactly at the cursor position.
        */}
        <svg
          width="36"
          height="36"
          viewBox="0 0 36 36"
          style={{ display: 'block', transform: 'translate(-13px, -13px)' }}
        >
          {/* ── Lens ─────────────────────────────────────────── */}
          {/* Subtle lens fill */}
          <circle cx="13" cy="13" r="10" fill="rgba(0,255,65,0.05)" />

          {/* Main ring */}
          <circle
            cx="13" cy="13" r="10"
            fill="none"
            stroke="#00ff41"
            strokeWidth="1.8"
          />

          {/* Crosshair ticks */}
          <line x1="13" y1="5"  x2="13" y2="8.5"  stroke="#00ff41" strokeWidth="1"   opacity="0.5" strokeLinecap="round" />
          <line x1="13" y1="17.5" x2="13" y2="21" stroke="#00ff41" strokeWidth="1"   opacity="0.5" strokeLinecap="round" />
          <line x1="5"  y1="13" x2="8.5"  y2="13" stroke="#00ff41" strokeWidth="1"   opacity="0.5" strokeLinecap="round" />
          <line x1="17.5" y1="13" x2="21" y2="13" stroke="#00ff41" strokeWidth="1"   opacity="0.5" strokeLinecap="round" />

          {/* Center dot */}
          <circle cx="13" cy="13" r="1.4" fill="#00ff41" opacity="0.7" />

          {/* Lens glare — short arc in upper-left quadrant */}
          <path
            d="M 7.2 9.8 Q 9.5 6.5 13 6.2"
            fill="none"
            stroke="#00ff41"
            strokeWidth="0.9"
            opacity="0.25"
            strokeLinecap="round"
          />

          {/* ── Handle ───────────────────────────────────────── */}
          {/* Handle exits at 45° from lens edge ≈ (20.1, 20.1) */}
          <line
            x1="20.1" y1="20.1"
            x2="33.5"  y2="33.5"
            stroke="#00ff41"
            strokeWidth="2.4"
            strokeLinecap="round"
          />
        </svg>
      </div>
    </div>
  )
}
