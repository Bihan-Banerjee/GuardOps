import { useEffect, useRef } from 'react'
import * as THREE from 'three'

export default function ParticleSphere() {
  const canvasRef = useRef(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const isMobile = window.innerWidth < 768
    const POINT_COUNT = isMobile ? 900 : 2500
    const LINE_DIST = 0.15

    // Scene setup
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(
      75,
      window.innerWidth / window.innerHeight,
      0.1,
      1000
    )
    camera.position.z = 4

    const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: false })
    renderer.setSize(window.innerWidth, window.innerHeight)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, isMobile ? 1 : 1.5))
    renderer.setClearColor(0x000000, 0)

    // Fibonacci lattice for even point distribution
    const positions = new Float32Array(POINT_COUNT * 3)
    const phi = Math.PI * (1 + Math.sqrt(5))
    for (let i = 0; i < POINT_COUNT; i++) {
      const theta = Math.acos(1 - (2 * i) / POINT_COUNT)
      const phiAngle = phi * i
      positions[i * 3]     = Math.sin(theta) * Math.cos(phiAngle)
      positions[i * 3 + 1] = Math.sin(theta) * Math.sin(phiAngle)
      positions[i * 3 + 2] = Math.cos(theta)
    }

    const pointGeo = new THREE.BufferGeometry()
    pointGeo.setAttribute('position', new THREE.BufferAttribute(positions, 3))

    const pointMat = new THREE.PointsMaterial({
      color: 0x00ff41,
      size: isMobile ? 0.022 : 0.015,
      transparent: true,
      opacity: 0.75,
      sizeAttenuation: true,
    })

    const points = new THREE.Points(pointGeo, pointMat)
    scene.add(points)

    // Line segments (desktop only)
    let lineSegments = null
    if (!isMobile) {
      const linePositions = []
      for (let i = 0; i < POINT_COUNT; i++) {
        for (let j = i + 1; j < POINT_COUNT; j++) {
          const dx = positions[i * 3]     - positions[j * 3]
          const dy = positions[i * 3 + 1] - positions[j * 3 + 1]
          const dz = positions[i * 3 + 2] - positions[j * 3 + 2]
          const dist = Math.sqrt(dx * dx + dy * dy + dz * dz)
          if (dist < LINE_DIST) {
            linePositions.push(
              positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2],
              positions[j * 3], positions[j * 3 + 1], positions[j * 3 + 2]
            )
          }
        }
      }

      const lineGeo = new THREE.BufferGeometry()
      lineGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(linePositions), 3))
      const lineMat = new THREE.LineBasicMaterial({
        color: 0x00ff41,
        transparent: true,
        opacity: 0.08,
      })
      lineSegments = new THREE.LineSegments(lineGeo, lineMat)
      scene.add(lineSegments)
    }

    // Scroll tracking
    let scrollY = 0
    const handleScroll = () => { scrollY = window.scrollY }
    window.addEventListener('scroll', handleScroll, { passive: true })

    // Resize
    const handleResize = () => {
      camera.aspect = window.innerWidth / window.innerHeight
      camera.updateProjectionMatrix()
      renderer.setSize(window.innerWidth, window.innerHeight)
    }
    window.addEventListener('resize', handleResize)

    // Animation loop
    let rafId
    let opacity = 0
    const animate = () => {
      rafId = requestAnimationFrame(animate)

      // Fade in on load
      if (opacity < 0.75) {
        opacity = Math.min(0.75, opacity + 0.008)
        pointMat.opacity = opacity
        if (lineSegments) lineSegments.material.opacity = opacity * 0.11
      }

      // Auto-rotation
      points.rotation.x += 0.0008
      points.rotation.y += 0.0015
      if (lineSegments) {
        lineSegments.rotation.x = points.rotation.x
        lineSegments.rotation.y = points.rotation.y
      }

      // Scroll-driven camera zoom — only active in first 2 viewports
      const heroHeight = window.innerHeight
      const scrollFraction = Math.min(scrollY / heroHeight, 1)
      const targetZ = 4 - scrollFraction * 1.6  // zoom from 4 → 2.4, never inside sphere
      camera.position.z += (targetZ - camera.position.z) * 0.05

      // Fade out sphere after hero to improve performance in lower sections
      const fadeStart = window.innerHeight * 0.8
      const fadeEnd = window.innerHeight * 1.4
      if (scrollY > fadeStart) {
        const fadeFraction = Math.min((scrollY - fadeStart) / (fadeEnd - fadeStart), 1)
        const targetOpacity = opacity * (1 - fadeFraction)
        pointMat.opacity = targetOpacity
        if (lineSegments) lineSegments.material.opacity = targetOpacity * 0.11
      }

      // Tilt camera slightly based on scroll
      camera.position.y += (-scrollY * 0.0002 - camera.position.y) * 0.05

      renderer.render(scene, camera)
    }
    animate()

    return () => {
      cancelAnimationFrame(rafId)
      window.removeEventListener('scroll', handleScroll)
      window.removeEventListener('resize', handleResize)
      pointGeo.dispose()
      pointMat.dispose()
      if (lineSegments) {
        lineSegments.geometry.dispose()
        lineSegments.material.dispose()
      }
      renderer.dispose()
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 w-full h-full"
      style={{ zIndex: -1, pointerEvents: 'none' }}
    />
  )
}
