/**
 * UI 표시용 리사이즈.
 * - 일반 사진: JPEG (용량)
 * - 누끼 PNG: 알파 유지 (JPEG 변환 시 검은 네모 박스 발생)
 */
export type DisplayImageOptions = {
  /** true면 PNG 알파 유지 (누끼 전용) */
  preserveAlpha?: boolean;
};

function isPngSource(source: string): boolean {
  return (
    source.startsWith("data:image/png") ||
    source.startsWith("data:image/webp")
  );
}

export async function createDisplayImageUrl(
  source: string,
  maxEdge = 480,
  options?: DisplayImageOptions
): Promise<string> {
  if (!source.startsWith("data:image/")) return source;

  const preserveAlpha = options?.preserveAlpha ?? isPngSource(source);

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      if (!w || !h || (w <= maxEdge && h <= maxEdge)) {
        resolve(source);
        return;
      }
      const scale = maxEdge / Math.max(w, h);
      const cw = Math.max(1, Math.round(w * scale));
      const ch = Math.max(1, Math.round(h * scale));
      const canvas = document.createElement("canvas");
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext("2d", preserveAlpha ? { alpha: true } : undefined);
      if (!ctx) {
        resolve(source);
        return;
      }
      if (preserveAlpha) {
        ctx.clearRect(0, 0, cw, ch);
      }
      ctx.drawImage(img, 0, 0, cw, ch);
      try {
        resolve(
          preserveAlpha
            ? canvas.toDataURL("image/png")
            : canvas.toDataURL("image/jpeg", 0.88)
        );
      } catch {
        resolve(source);
      }
    };
    img.onerror = () => resolve(source);
    img.src = source;
  });
}

/** 누끼·투명 PNG 전용 (테마/미리보기 오버레이) */
export function createDisplayCutoutUrl(
  source: string,
  maxEdge = 512
): Promise<string> {
  return createDisplayImageUrl(source, maxEdge, { preserveAlpha: true });
}
