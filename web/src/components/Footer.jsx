import { Shield, Github, ExternalLink } from 'lucide-react'

export default function Footer() {
  return (
    <footer className="relative bg-[#030303]/82 border-t border-zinc-900 py-12 px-4">
      <div className="max-w-7xl mx-auto">
        <div className="flex flex-col md:flex-row items-center justify-between gap-6">
          {/* Brand */}
          <div className="flex items-center gap-3">
            <Shield className="w-5 h-5 text-terminal/50" strokeWidth={1.5} />
            <div className="font-mono text-sm">
              <span className="text-terminal/60 font-bold tracking-widest">GUARD</span>
              <span className="text-zinc-600 font-bold tracking-widest">OPS</span>
              <span className="ml-2 text-zinc-700">v0.13.0</span>
            </div>
          </div>

          {/* Links */}
          <div className="flex items-center gap-6 text-xs font-mono">
            <a
              href="https://github.com/Bihan-Banerjee/GuardOps"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-zinc-600 hover:text-terminal transition-colors"
            >
              <Github className="w-3.5 h-3.5" />
              GitHub
            </a>
            <a
              href="https://pypi.org/project/guardops"
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-zinc-600 hover:text-terminal transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              PyPI
            </a>
          </div>

          {/* Copyright */}
          <p className="text-xs font-mono text-zinc-700">
            © 2026 GuardOps · MIT License
          </p>
        </div>

        {/* Terminal line */}
        <div className="mt-8 pt-6 border-t border-zinc-900 text-center">
          <p className="text-xs font-mono text-zinc-800">
            <span className="text-zinc-700">$</span>{' '}
            guardops --version{' '}
            <span className="text-terminal/30">→ 0.13.0</span>
          </p>
        </div>
      </div>
    </footer>
  )
}
