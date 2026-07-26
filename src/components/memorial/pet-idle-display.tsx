"use client";

import { resolveIdleDisplaySource } from "@/lib/device-host-flags";
import { CutoutIdleMotion } from "@/components/memorial/cutout-idle-motion";
import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";

interface PetIdleDisplayProps {
  idleVideoUrl?: string | null;
  cutoutUrl?: string | null;
  className?: string;
  style?: React.CSSProperties;
  preload?: "none" | "metadata" | "auto";
  allowDemoFallback?: boolean;
}

/** 사용자 idle mp4 또는 cutout 정적 애니 — Goya 데모는 cutout 있을 때 표시하지 않음 */
export function PetIdleDisplay({
  idleVideoUrl,
  cutoutUrl,
  className,
  style,
  preload = "metadata",
  allowDemoFallback,
}: PetIdleDisplayProps) {
  const display = resolveIdleDisplaySource(idleVideoUrl, cutoutUrl, {
    allowDemoFallback,
  });
  if (!display) return null;

  if (display.mode === "video") {
    return (
      <IdleLoopVideo
        src={display.src}
        className={className}
        style={style}
        preload={preload}
      />
    );
  }

  return <CutoutIdleMotion src={display.src} className={className} style={style} />;
}
