"use client";

import { useMemo, type CSSProperties } from "react";

const DOT_COUNT = 18;

function seededRandom(seed: number) {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/** 하단에서 올라오는 골드 파티클 — memorial-ui 전역 배경 (CSS only, lite UI에서도 유지) */
export function MemorialSparkleField() {
  const dots = useMemo(
    () =>
      Array.from({ length: DOT_COUNT }, (_, i) => ({
        id: i,
        left: seededRandom(i * 3) * 100,
        opacity: 0.22 + seededRandom(i * 3 + 2) * 0.35,
        size: 2.5 + seededRandom(i * 3 + 1) * 2.2,
        duration: 6 + seededRandom(i * 7) * 6,
        delay: seededRandom(i * 11) * 6,
      })),
    [],
  );

  return (
    <div className="memorial-sparkle-field" aria-hidden>
      <style>{`
        @keyframes memorial-sparkle-rise {
          0% { transform: translateY(0); opacity: 0; }
          10% { opacity: 0.7; }
          90% { opacity: 0.7; }
          100% { transform: translateY(-105vh); opacity: 0; }
        }
      `}</style>
      {dots.map((d) => (
        <span
          key={d.id}
          className="home-dot"
          style={
            {
              left: `${d.left}%`,
              width: `${d.size}px`,
              height: `${d.size}px`,
              animation: `memorial-sparkle-rise ${d.duration}s linear ${d.delay}s infinite`,
            } as CSSProperties
          }
        />
      ))}
    </div>
  );
}
