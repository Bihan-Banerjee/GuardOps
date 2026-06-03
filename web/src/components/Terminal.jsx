import { useState, useEffect, useImperativeHandle, forwardRef, useRef } from 'react'

const Terminal = forwardRef(function Terminal({ lines = [], className = '' }, ref) {
  const [displayedLines, setDisplayedLines] = useState([])
  const [currentLine, setCurrentLine] = useState(0)
  const [currentChar, setCurrentChar] = useState(0)
  const [isTyping, setIsTyping] = useState(false)
  const [showCursor, setShowCursor] = useState(false)
  const containerRef = useRef(null)
  const typingRef = useRef(null)

  useImperativeHandle(ref, () => ({
    startTyping() {
      setIsTyping(true)
      setShowCursor(true)
    }
  }))

  useEffect(() => {
    if (!isTyping) return
    const line = lines[currentLine]
    if (!line) return

    const isPrompt = line.text.startsWith('$')
    const charDelay = isPrompt ? 55 : 18
    const lineDelay = isPrompt ? 420 : 100

    if (currentChar < line.text.length) {
      typingRef.current = setTimeout(() => {
        setDisplayedLines(prev => {
          const next = [...prev]
          if (!next[currentLine]) {
            next[currentLine] = { ...line, text: '' }
          }
          next[currentLine] = { ...line, text: line.text.slice(0, currentChar + 1) }
          return next
        })
        setCurrentChar(c => c + 1)
      }, charDelay)
    } else {
      typingRef.current = setTimeout(() => {
        setCurrentLine(l => l + 1)
        setCurrentChar(0)
        if (containerRef.current) {
          containerRef.current.scrollTop = containerRef.current.scrollHeight
        }
      }, lineDelay)
    }

    return () => clearTimeout(typingRef.current)
  }, [isTyping, currentLine, currentChar, lines])

  const colorClass = (color) => {
    switch (color) {
      case 'green': return 'text-terminal'
      case 'red': return 'text-red-400'
      case 'yellow': return 'text-yellow-400'
      case 'blue': return 'text-blue-400'
      case 'cyan': return 'text-cyan-400'
      case 'dim': return 'text-zinc-500'
      case 'white': return 'text-zinc-200'
      default: return 'text-zinc-400'
    }
  }

  return (
    <div className={`rounded-lg overflow-hidden border border-terminal/20 bg-black/80 backdrop-blur-sm ${className}`}>
      {/* Chrome bar */}
      <div className="flex items-center gap-2 px-4 py-3 bg-zinc-900/80 border-b border-zinc-800">
        <div className="w-3 h-3 rounded-full bg-red-500/80" />
        <div className="w-3 h-3 rounded-full bg-yellow-500/80" />
        <div className="w-3 h-3 rounded-full bg-terminal/80" />
        <span className="ml-3 text-xs text-zinc-500 font-mono">guardops — terminal</span>
      </div>

      {/* Output area */}
      <div
        ref={containerRef}
        className="p-4 font-mono text-sm min-h-[200px] max-h-[420px] overflow-y-auto space-y-1"
        style={{ scrollBehavior: 'smooth' }}
      >
        {displayedLines.map((line, i) => (
          <div key={i} className={`leading-relaxed ${colorClass(line.color)}`}>
            {line.text}
            {i === currentLine - 1 && currentLine < lines.length && isTyping && (
              <span className="inline-block w-2 h-4 bg-terminal cursor-blink ml-0.5 align-middle" />
            )}
          </div>
        ))}
        {showCursor && currentLine < lines.length && (
          <div className="text-terminal">
            {currentLine < lines.length && lines[currentLine]?.text.startsWith('$') ? (
              <span className="text-zinc-500">
                {'> '}<span className="inline-block w-2 h-4 bg-terminal cursor-blink align-middle" />
              </span>
            ) : null}
          </div>
        )}
        {currentLine >= lines.length && showCursor && (
          <div className="text-terminal">
            <span className="text-zinc-500">&gt; </span>
            <span className="inline-block w-2 h-4 bg-terminal cursor-blink align-middle" />
          </div>
        )}
      </div>
    </div>
  )
})

export default Terminal
