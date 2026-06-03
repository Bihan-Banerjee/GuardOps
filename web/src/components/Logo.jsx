// GuardOps shield logo (inline SVG so it scales + keeps the blinking cursor).
// `className` controls the rendered size (e.g. "w-8 h-8").
export default function Logo({ className = '', animate = true }) {
  return (
    <svg
      viewBox="0 0 400 400"
      className={className}
      role="img"
      aria-label="GuardOps logo"
    >
      {/* Deep black terminal background */}
      <rect width="400" height="400" fill="#0D1117" rx="60" />

      {/* Subtle grid pattern for cyber feel */}
      <path
        d="M 0 100 L 400 100 M 0 200 L 400 200 M 0 300 L 400 300 M 100 0 L 100 400 M 200 0 L 200 400 M 300 0 L 300 400"
        stroke="#161B22"
        strokeWidth="2"
        fill="none"
      />

      {/* Neon green shield outline */}
      <path
        d="M 200 60 L 90 110 L 90 240 C 90 310 200 360 200 360 C 200 360 310 310 310 240 L 310 110 Z"
        fill="#0D1117"
        stroke="#00FF41"
        strokeWidth="16"
        strokeLinejoin="round"
      />

      {/* Terminal prompt inside the shield */}
      <text
        x="140"
        y="235"
        fontFamily="'Courier New', monospace"
        fontSize="85"
        fill="#00FF41"
        fontWeight="bold"
      >
        &gt;_
      </text>

      {/* Glowing cursor node — height matches the ">" character cap height */}
      <rect x="250" y="185" width="18" height="50" fill="#00FF41">
        {animate && (
          <animate
            attributeName="opacity"
            values="1;0;1"
            dur="1s"
            repeatCount="indefinite"
          />
        )}
      </rect>
    </svg>
  )
}
