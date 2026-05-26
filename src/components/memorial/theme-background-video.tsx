"use client";

import { useEffect, useRef } from "react";

interface ThemeBackgroundVideoProps {
  src: string;
  className?: string;
  poster?: string;
}

/** 테마 프리뷰용 루프 배경 (음소거, 인라인 재생) */
export function ThemeBackgroundVideo({ src, className = "", poster }: ThemeBackgroundVideoProps) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.muted = true;
    el.playsInline = true;
    const play = () => {
      void el.play().catch(() => {
        /* autoplay policy — 사용자 탭 후 재생될 수 있음 */
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
      poster={poster}
      className={`absolute inset-0 h-full w-full object-cover ${className}`}
      autoPlay
      loop
      muted
      playsInline
      preload="metadata"
    />
  );
}
