import ParticleSphere from './components/ParticleSphere.jsx'
import CustomCursor from './components/CustomCursor.jsx'
import Navbar from './components/Navbar.jsx'
import OfflineBanner from './components/OfflineBanner.jsx'
import Hero from './components/Hero.jsx'
import Trends from './components/Trends.jsx'
import Runs from './components/Runs.jsx'
import Findings from './components/Findings.jsx'
import Runtime from './components/Runtime.jsx'
import Metrics from './components/Metrics.jsx'
import Footer from './components/Footer.jsx'

export default function App() {
  return (
    <>
      {/* Fixed Three.js canvas — behind everything */}
      <ParticleSphere />

      {/* Custom magnifying glass cursor */}
      <CustomCursor />

      {/* Sticky navigation */}
      <Navbar />

      {/* Shown only when the live API is down and we're serving the snapshot */}
      <OfflineBanner />

      {/* Page content — solid backgrounds prevent sphere bleed-through */}
      <main>
        <Hero />
        <Trends />
        <Runs />
        <Findings />
        <Runtime />
        <Metrics />
        <Footer />
      </main>
    </>
  )
}
