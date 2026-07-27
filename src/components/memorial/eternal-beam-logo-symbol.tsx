"use client";

import { useId } from "react";

/** Eternal Beam 심볼 — SVG 그라데이션 (배경·박스 없음) */
export function EternalBeamLogoSymbol({
  size = 88,
  className = "",
  sharp = false,
}: {
  size?: number;
  className?: string;
  /** 스플래시: 중앙 빔 선명 */
  sharp?: boolean;
}) {
  const uid = useId().replace(/:/g, "");
  const left = `eb-left-${uid}`;
  const right = `eb-right-${uid}`;
  const core = `eb-core-${uid}`;

  // viewBox 타이트 — 상단 여백·네모 박스 느낌 제거
  const viewW = 60;
  const viewH = sharp ? 44 : 52;
  const viewY = sharp ? 18 : 14;

  return (
    <svg
      width={size}
      height={Math.round(size * (viewH / viewW))}
      viewBox={`10 ${viewY} ${viewW} ${viewH}`}
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

      {/* 윗변: 좌·우 페탈 각각만 — 가운데 연결 바 없음 */}
      <path
        d="M14 24 H34 C32 24 29 28 27 34 C25 42 28 50 40 58 C22 52 16 42 14 32 C13 28 14 24 14 24 Z"
        fill={`url(#${left})`}
      />
      <path
        d="M66 24 H46 C48 24 51 28 53 34 C55 42 52 50 40 58 C58 52 64 42 66 32 C67 28 66 24 66 24 Z"
        fill={`url(#${right})`}
      />
      <ellipse cx="40" cy="34" rx={sharp ? 7 : 9} ry={sharp ? 10.5 : 13} fill={`url(#${core})`} />
    </svg>
  );
}
