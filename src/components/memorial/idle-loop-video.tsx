"use client";

import { useEffect, useRef } from "react";

interface IdleLoopVideoProps {
  src: string;
  className?: string;
  style?: React.CSSProperties;
}

/** Luma idle 루프 — muted/playsInline/loop + autoplay 재시도 */
export function IdleLoopVideo({ src, className = "", style }: IdleLoopVideoProps) {
  const ref = useRef<HTMLVideoElement>(null);
  const needsCrossOrigin = src.startsWith("http://") || src.startsWith("https://");

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
      preload="auto"
      crossOrigin={needsCrossOrigin ? "anonymous" : undefined}
    />
  );
}
