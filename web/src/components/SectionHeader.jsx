import { useRef, useEffect } from 'react'
import anime from 'animejs/lib/anime.es.js'
import { useInView } from '../hooks/useInView.js'

export default function SectionHeader({ children }) {
  const ref = useRef(null)
  const inView = useInView(ref)
  const animated = useRef(false)

  useEffect(() => {
    if (!inView || animated.current) return
    animated.current = true
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
      <div className="flex gap-0.5 flex-wrap">
        {children.split('').map((ch, i) =>
          ch === ' ' ? (
            // Non-breaking space to preserve word gaps
            <span key={i} className="sh-letter inline-block opacity-0" style={{ minWidth: '0.4em' }}>
              &nbsp;
            </span>
          ) : (
            <span
              key={i}
              className="sh-letter inline-block font-black text-2xl sm:text-3xl font-mono tracking-[0.12em] opacity-0"
              style={{ color: '#e4e4e7' }}
            >
              {ch}
            </span>
          )
        )}
      </div>
      <div className="flex-1 h-px bg-gradient-to-r from-terminal/30 to-transparent" />
    </div>
  )
}
