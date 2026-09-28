"use client";

import type { ImgHTMLAttributes } from "react";

/**
 * 사용자 펫 사진/누끼를 그리는 **유일한** 자리.
 *
 * 브랜드 사진(홈 히어로, 빔 카드, 테마 카탈로그 썸네일)은 여기를 거치지 않는다 —
 * 그쪽은 의도된 cover 크롭이다. 이 컴포넌트는 사용자가 올린 미디어에만 쓴다.
 *
 * variant
 * - "full"   : 펫 전체가 보인다(contain). 남는 공간은 따뜻한 중립색 위에 같은
 *              사진을 흐리게 깔아 세로/가로/정사각 어느 사진도 레터박스가 되지
 *              않는다. 누끼(투명 PNG/WebP)는 흐린 배경 없이 중립색만 깐다.
 * - "avatar" : 작은 원형 신원 칩. cover 를 허용하되 상체 쪽(50% 28%)으로
 *              치우쳐 얼굴이 원 안에 남게 한다.
 */
export type PetPhotoVariant = "full" | "avatar";

type PetPhotoProps = {
  src: string;
  alt?: string;
  variant?: PetPhotoVariant;
  /** 바깥 프레임에 얹을 클래스. 프레임은 부모 크기를 100% 채운다. */
  className?: string;
  /**
   * 흐린 배경을 강제로 켜거나 끈다. 생략하면 src 가 투명 가능 포맷(PNG/WebP)이면
   * 끄고, 그 외(JPEG·HEIC·blob:)는 켠다.
   */
  backdrop?: boolean;
  loading?: ImgHTMLAttributes<HTMLImageElement>["loading"];
  /**
   * 사진의 실제 가로/세로 비(naturalWidth / naturalHeight)를 알려 준다.
   * 프레임을 사진 모양에 맞추고 싶은 화면(인테이크 카드)이 쓴다.
   */
  onAspect?: (ratio: number) => void;
};

/** 투명 채널이 있을 법한 소스 — 누끼 PNG/WebP. blob: 은 알 수 없으니 사진으로 본다. */
export function looksTransparent(src: string): boolean {
  if (src.startsWith("data:image/png") || src.startsWith("data:image/webp")) return true;
  if (src.startsWith("data:")) return false;
  const path = src.split(/[?#]/, 1)[0].toLowerCase();
  return path.endsWith(".png") || path.endsWith(".webp");
}

export function PetPhoto({
  src,
  alt = "",
  variant = "full",
  className = "",
  backdrop,
  loading,
  onAspect,
}: PetPhotoProps) {
  const isAvatar = variant === "avatar";
  const showBackdrop = !isAvatar && (backdrop ?? !looksTransparent(src));
  const frameClass = `eb-pet-photo${isAvatar ? " eb-pet-photo--avatar" : ""}${
    showBackdrop ? " eb-pet-photo--backdrop" : ""
  }${className ? ` ${className}` : ""}`;

  return (
    <span className={frameClass} data-pet-photo={variant}>
      {showBackdrop ? (
        <img src={src} alt="" aria-hidden className="eb-pet-photo__backdrop" decoding="async" loading={loading} />
      ) : null}
      <img
        src={src}
        alt={alt}
        className="eb-pet-photo__img"
        decoding="async"
        loading={loading}
        onLoad={
          onAspect
            ? (e) => {
                const img = e.currentTarget;
                if (img.naturalWidth > 0 && img.naturalHeight > 0) {
                  onAspect(img.naturalWidth / img.naturalHeight);
                }
              }
            : undefined
        }
      />
    </span>
  );
}
