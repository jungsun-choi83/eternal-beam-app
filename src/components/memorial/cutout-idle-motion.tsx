"use client";

interface CutoutIdleMotionProps {
  src: string;
  className?: string;
  style?: React.CSSProperties;
}

/** Luma idle 대기 중 — 사용자 cutout에 미세 호흡·흔들림 CSS 애니메이션 */
export function CutoutIdleMotion({ src, className = "", style }: CutoutIdleMotionProps) {
  return (
    <img
      src={src}
      alt=""
      className={`cutout-idle-motion ${className}`.trim()}
      style={style}
      decoding="async"
      draggable={false}
    />
  );
}
