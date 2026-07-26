"use client";

import type { ReactNode } from "react";

interface CutoutStageProps {
  children: ReactNode;
  className?: string;
  /** contain = 누끼 PNG, cover = 사진 미리보기 */
  fit?: "contain" | "cover";
  /** true면 체커보드(모자이크) 없이 단색 배경 */
  plain?: boolean;
}

/** 투명 PNG(누끼)를 배경 + 소프트 글로우 위에 표시 */
export function CutoutStage({
  children,
  className = "",
  fit = "contain",
  plain = false,
}: CutoutStageProps) {
  return (
    <div
      className={`cutout-stage ${plain ? "cutout-stage--plain" : ""} ${fit === "cover" ? "cutout-stage--fill" : ""} ${className}`}
    >
      <div className="cutout-stage__glow" aria-hidden />
      {children}
    </div>
  );
}
