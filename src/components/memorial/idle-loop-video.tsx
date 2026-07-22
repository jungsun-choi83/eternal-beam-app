"use client";

import { useEffect, useRef } from "react";

interface IdleLoopVideoProps {
  src: string;
  className?: string;
  style?: React.CSSProperties;
  /** metadata = 빠른 첫 프레임, auto = 전체 프리로드 */
  preload?: "none" | "metadata" | "auto";
}

/** Luma idle 루프 — muted/playsInline/loop + autoplay 재시도 */
export function IdleLoopVideo({
  src,
  className = "",
  style,
  preload = "metadata",
}: IdleLoopVideoProps) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.muted = true;
    el.playsInline = true;
    const play = () => {
      void el.play().catch(() => {
        /* autoplay policy */
      });
    };
    play();
    el.addEventListener("loadeddata", play);
    return () => el.removeEventListener("loadeddata", play);
  }, [src]);

  return (
    <video
      ref={ref}
      src={src}
      className={className}
      style={style}
      autoPlay
      loop
      muted
      playsInline
      preload={preload}
    />
  );
}
