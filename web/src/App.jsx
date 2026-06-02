import ParticleSphere from './components/ParticleSphere.jsx'
import Navbar from './components/Navbar.jsx'
import Hero from './components/Hero.jsx'
import Overview from './components/Overview.jsx'
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

      {/* Sticky navigation */}
      <Navbar />

      {/* Page content — solid backgrounds prevent sphere bleed-through */}
      <main>
        <Hero />
        <Overview />
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
