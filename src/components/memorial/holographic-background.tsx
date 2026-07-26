"use client";

import { isLiteUI } from "@/lib/ui-performance";

/** 홈 등 배경 — lite 모드에서는 정적 그라데이션만 */
export function HolographicBackground() {
  const lite = isLiteUI();

  if (lite) {
    return (
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute inset-0 opacity-80"
          style={{
            background:
              "radial-gradient(circle at 35% 45%, rgba(212, 175, 55, 0.09) 0%, transparent 55%)",
          }}
        />
      </div>
    );
  }

  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 30% 50%, rgba(212, 175, 55, 0.1) 0%, transparent 50%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-60"
        style={{
          background:
            "radial-gradient(ellipse at 70% 30%, rgba(184, 134, 11, 0.06) 0%, transparent 40%)",
        }}
      />
    </div>
  );
}
