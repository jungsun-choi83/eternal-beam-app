"use client";

import { isLiteUI } from "@/lib/ui-performance";

/**
 * 홈 등 배경 — 아이보리 위에 아주 약한 세이지/크림 빛.
 * lite 모드에서는 정적 그라데이션 한 장만.
 */
export function HolographicBackground() {
  const lite = isLiteUI();

  if (lite) {
    return (
      <div className="absolute inset-0 overflow-hidden pointer-events-none">
        <div
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(circle at 35% 45%, rgba(143, 159, 134, 0.16) 0%, transparent 55%)",
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
            "radial-gradient(circle at 30% 50%, rgba(143, 159, 134, 0.16) 0%, transparent 50%)",
        }}
      />
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse at 70% 30%, rgba(216, 191, 138, 0.18) 0%, transparent 40%)",
        }}
      />
    </div>
  );
}
