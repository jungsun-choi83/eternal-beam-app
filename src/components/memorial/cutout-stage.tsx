"use client";

import type { ReactNode } from "react";

interface CutoutStageProps {
  children: ReactNode;
  className?: string;
  /** contain = 누끼 PNG, cover = 사진 미리보기 */
  fit?: "contain" | "cover";
}

/** 투명 PNG(누끼)를 체커보드 + 소프트 글로우 위에 표시 */
export function CutoutStage({
  children,
  className = "",
  fit = "contain",
}: CutoutStageProps) {
  return (
    <div
      className={`cutout-stage ${fit === "cover" ? "cutout-stage--fill" : ""} ${className}`}
    >
      <div className="cutout-stage__glow" aria-hidden />
      {children}
    </div>
  );
}
