import { useState, useEffect } from 'react'

export function useInView(ref, options = {}) {
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const el = ref.current
    if (!el) return

    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setInView(true)
          obs.disconnect() // fire once
        }
      },
      { threshold: 0.15, ...options }
    )
    obs.observe(el)
    return () => obs.disconnect()
  }, [ref]) // eslint-disable-line react-hooks/exhaustive-deps

  return inView
}
