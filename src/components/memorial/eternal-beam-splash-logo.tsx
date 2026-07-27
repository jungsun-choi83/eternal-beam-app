"use client";

import { EternalBeamLogoSymbol } from "@/components/memorial/eternal-beam-logo-symbol";

/** 1P 스플래시 — 래퍼·글로우·PNG 없이 로고만 */
export function EternalBeamSplashLogo() {
  return (
    <div className="splash-brand-mark flex flex-col items-center gap-3">
      <EternalBeamLogoSymbol size={128} variant="splash" className="splash-brand-symbol" />
      <p className="splash-brand-title m-0">Eternal Beam</p>
    </div>
  );
}
