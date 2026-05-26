/**
 * UI 표시용 썸네일 — 큰 data URL을 여러 번 그리면 모바일·에뮬레이터가 심하게 끊김.
 */
export async function createDisplayImageUrl(
  source: string,
  maxEdge = 480
): Promise<string> {
  if (!source.startsWith("data:image/")) return source;

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
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(source);
        return;
      }
      ctx.drawImage(img, 0, 0, cw, ch);
      try {
        resolve(canvas.toDataURL("image/jpeg", 0.88));
      } catch {
        resolve(source);
      }
    };
    img.onerror = () => resolve(source);
    img.src = source;
  });
}
