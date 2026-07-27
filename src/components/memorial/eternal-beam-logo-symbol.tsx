"use client";

/** Eternal Beam 심볼 — SVG (PNG 누끼 깨짐 대체) */
export function EternalBeamLogoSymbol({
  size = 88,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 80 72"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden
    >
      <defs>
        <linearGradient id="eb-left" x1="20%" y1="0%" x2="80%" y2="100%">
          <stop offset="0%" stopColor="#f8f0dc" />
          <stop offset="45%" stopColor="#e8d5a3" />
          <stop offset="100%" stopColor="#b8922a" />
        </linearGradient>
        <linearGradient id="eb-right" x1="80%" y1="0%" x2="20%" y2="100%">
          <stop offset="0%" stopColor="#f8f0dc" />
          <stop offset="45%" stopColor="#e8d5a3" />
          <stop offset="100%" stopColor="#b8922a" />
        </linearGradient>
        <radialGradient id="eb-core" cx="50%" cy="46%" r="28%">
          <stop offset="0%" stopColor="#fffef8" stopOpacity="0.95" />
          <stop offset="55%" stopColor="#f5e6b8" stopOpacity="0.55" />
          <stop offset="100%" stopColor="#d4af37" stopOpacity="0" />
        </radialGradient>
        <filter id="eb-glow" x="-40%" y="-40%" width="180%" height="180%">
          <feGaussianBlur stdDeviation="1.8" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>

      <ellipse cx="40" cy="34" rx="11" ry="15" fill="url(#eb-core)" />

      <path
        d="M40 6 C24 10 14 24 16 40 C18 50 26 58 40 62 C34 48 32 32 40 6 Z"
        fill="url(#eb-left)"
        filter="url(#eb-glow)"
      />
      <path
        d="M40 6 C56 10 66 24 64 40 C62 50 54 58 40 62 C46 48 48 32 40 6 Z"
        fill="url(#eb-right)"
        filter="url(#eb-glow)"
      />
    </svg>
  );
}
