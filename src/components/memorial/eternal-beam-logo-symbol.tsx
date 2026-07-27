"use client";

import { useId } from "react";

const PETAL_LEFT =
  "M14 24 H34 C32 24 29 28 27 34 C25 42 28 50 40 58 C22 52 16 42 14 32 C13 28 14 24 14 24 Z";
const PETAL_RIGHT =
  "M66 24 H46 C48 24 51 28 53 34 C55 42 52 50 40 58 C58 52 64 42 66 32 C67 28 66 24 66 24 Z";

/** Eternal Beam 심볼 — SVG 그라데이션 (배경·박스 없음) */
export function EternalBeamLogoSymbol({
  size = 88,
  className = "",
  variant = "default",
}: {
  size?: number;
  className?: string;
  /** icon: 하단·배지용 작은 크기 / splash: 1P 스플래시 */
  variant?: "default" | "icon" | "splash";
}) {
  const uid = useId().replace(/:/g, "");
  const left = `eb-left-${uid}`;
  const right = `eb-right-${uid}`;
  const core = `eb-core-${uid}`;

  const isSplash = variant === "splash";
  const isIcon = variant === "icon";
  const viewW = isSplash ? 60 : 80;
  const viewH = isSplash ? 44 : 72;
  const viewX = isSplash ? 10 : 0;
  const viewY = isSplash ? 18 : 0;
  const width = size;
  const height = Math.round(size * (viewH / viewW));

  return (
    <svg
      width={width}
      height={height}
      viewBox={`${viewX} ${viewY} ${viewW} ${viewH}`}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden
    >
      <defs>
        <linearGradient id={left} x1="20%" y1="35%" x2="85%" y2="100%">
          <stop offset="0%" stopColor="#e8d5a3" />
          <stop offset="45%" stopColor="#d4af37" />
          <stop offset="100%" stopColor="#8a6b1a" />
        </linearGradient>
        <linearGradient id={right} x1="80%" y1="35%" x2="15%" y2="100%">
          <stop offset="0%" stopColor="#e8d5a3" />
          <stop offset="45%" stopColor="#d4af37" />
          <stop offset="100%" stopColor="#8a6b1a" />
        </linearGradient>
        <radialGradient id={core} cx="50%" cy="46%" r="28%">
          <stop offset="0%" stopColor="#fffef8" stopOpacity="1" />
          <stop offset="35%" stopColor="#fff6dc" stopOpacity="0.95" />
          <stop offset="62%" stopColor="#f5e6b8" stopOpacity="0.45" />
          <stop offset="100%" stopColor="#d4af37" stopOpacity="0" />
        </radialGradient>
      </defs>

      <path d={PETAL_LEFT} fill={`url(#${left})`} />
      <path d={PETAL_RIGHT} fill={`url(#${right})`} />
      <ellipse
        cx="40"
        cy={isIcon ? 33 : 34}
        rx={isSplash ? 7 : isIcon ? 8 : 9}
        ry={isSplash ? 10.5 : isIcon ? 11.5 : 13}
        fill={`url(#${core})`}
      />
    </svg>
  );
}
