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

/** idle mp4(데모 목업 포함) 또는 cutout 정적 — 데모 off일 때만 정적 */
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
