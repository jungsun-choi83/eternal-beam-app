/**
 * UI 표시용 리사이즈.
 * - 일반 사진: JPEG (용량)
 * - 누끼 PNG: 알파 유지 (JPEG 변환 시 검은 네모 박스 발생)
 */
export type DisplayImageOptions = {
  /** true면 PNG 알파 유지 (누끼 전용) */
  preserveAlpha?: boolean;
  /** true면 투명 여백 크롭 (누끼 미리보기) */
  trimAlpha?: boolean;
};

function isPngSource(source: string): boolean {
  return (
    source.startsWith("data:image/png") ||
    source.startsWith("data:image/webp")
  );
}

/** 투명 여백 제거 — 누끼 PNG가 프레임 안에서 작게 보이는 문제 완화 */
function trimCanvasAlpha(
  canvas: HTMLCanvasElement,
  ctx: CanvasRenderingContext2D,
  threshold = 12
): HTMLCanvasElement {
  const { width, height } = canvas;
  if (!width || !height) return canvas;

  let imageData: ImageData;
  try {
    imageData = ctx.getImageData(0, 0, width, height);
  } catch {
    return canvas;
  }

  const { data } = imageData;
  let minX = width;
  let minY = height;
  let maxX = 0;
  let maxY = 0;
  let found = false;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const a = data[(y * width + x) * 4 + 3];
      if (a > threshold) {
        found = true;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }

  if (!found) return canvas;

  const pad = Math.round(Math.max(maxX - minX, maxY - minY) * 0.04);
  minX = Math.max(0, minX - pad);
  minY = Math.max(0, minY - pad);
  maxX = Math.min(width - 1, maxX + pad);
  maxY = Math.min(height - 1, maxY + pad);

  const cw = maxX - minX + 1;
  const ch = maxY - minY + 1;
  if (cw <= 0 || ch <= 0 || (cw === width && ch === height)) return canvas;

  const trimmed = document.createElement("canvas");
  trimmed.width = cw;
  trimmed.height = ch;
  const tctx = trimmed.getContext("2d", { alpha: true });
  if (!tctx) return canvas;
  tctx.drawImage(canvas, minX, minY, cw, ch, 0, 0, cw, ch);
  return trimmed;
}

function canvasToDisplayUrl(
  canvas: HTMLCanvasElement,
  preserveAlpha: boolean
): string {
  try {
    return preserveAlpha
      ? canvas.toDataURL("image/png")
      : canvas.toDataURL("image/jpeg", 0.88);
  } catch {
    return "";
  }
}

export async function createDisplayImageUrl(
  source: string,
  maxEdge = 480,
  options?: DisplayImageOptions
): Promise<string> {
  if (!source.startsWith("data:image/")) return source;

  const preserveAlpha = options?.preserveAlpha ?? isPngSource(source);
  const trimAlpha = options?.trimAlpha ?? preserveAlpha;

  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const w = img.naturalWidth;
      const h = img.naturalHeight;
      if (!w || !h) {
        resolve(source);
        return;
      }

      let cw = w;
      let ch = h;
      if (Math.max(w, h) > maxEdge) {
        const scale = maxEdge / Math.max(w, h);
        cw = Math.max(1, Math.round(w * scale));
        ch = Math.max(1, Math.round(h * scale));
      }

      const canvas = document.createElement("canvas");
      canvas.width = cw;
      canvas.height = ch;
      const ctx = canvas.getContext("2d", preserveAlpha ? { alpha: true } : undefined);
      if (!ctx) {
        resolve(source);
        return;
      }
      if (preserveAlpha) ctx.clearRect(0, 0, cw, ch);
      ctx.drawImage(img, 0, 0, cw, ch);

      const outCanvas = trimAlpha ? trimCanvasAlpha(canvas, ctx) : canvas;
      // [IMAGE-TRACE] 표시용 축소 + 알파 여백 크롭. trimAlpha 는 상수가 아니라
      // 피사체 bbox 크기에 따라 임의의 값(예: 225)을 만들어 낸다.
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.log(
          `%c[IMAGE-TRACE]%c display-resize                ${w}x${h} -> ${cw}x${ch}` +
            (trimAlpha ? ` -> trimAlpha ${outCanvas.width}x${outCanvas.height}` : "") +
            `  (maxEdge=${maxEdge}) origin=display-thumbnail`,
          "color:#c9a227;font-weight:bold",
          "color:inherit"
        );
      }
      const url = canvasToDisplayUrl(outCanvas, preserveAlpha);
      resolve(url || source);
    };
    img.onerror = () => resolve(source);
    img.src = source;
  });
}

async function sourceToDataUrl(source: string): Promise<string> {
  if (source.startsWith("data:image/")) return source;
  if (!/^https?:\/\//i.test(source)) return source;
  try {
    const res = await fetch(source);
    if (!res.ok) return source;
    const blob = await res.blob();
    return await new Promise<string>((resolve) => {
      const reader = new FileReader();
      reader.onload = () =>
        resolve(typeof reader.result === "string" ? reader.result : source);
      reader.onerror = () => resolve(source);
      reader.readAsDataURL(blob);
    });
  } catch {
    return source;
  }
}

/** 누끼·투명 PNG 전용 (테마/미리보기 오버레이) */
export async function createDisplayCutoutUrl(
  source: string,
  maxEdge = 640
): Promise<string> {
  const normalized = await sourceToDataUrl(source);
  return createDisplayImageUrl(normalized, maxEdge, {
    preserveAlpha: true,
    trimAlpha: true,
  });
}
