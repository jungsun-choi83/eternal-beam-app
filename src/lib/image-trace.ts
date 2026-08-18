/**
 * [IMAGE-TRACE] 임시 계측 — 업로드 이미지가 어느 단계에서 축소되는지 추적.
 *
 * 목적: "1200x1200 을 올렸는데 백엔드는 225x225 를 받았다"는 현상의 발생 지점을
 * 추측이 아니라 측정으로 특정한다. 원인 확정 후 이 파일과 호출부
 * (`// [IMAGE-TRACE]` 주석이 붙은 줄)를 통째로 제거할 것.
 *
 * 켜기:  .env.local 에 VITE_IMAGE_TRACE=1  (DEV 에서는 기본 ON)
 * 끄기:  VITE_IMAGE_TRACE=0
 */

export const IMAGE_TRACE_ENABLED = (() => {
  const v = String(import.meta.env.VITE_IMAGE_TRACE ?? "").trim();
  if (v === "1") return true;
  if (v === "0") return false;
  return Boolean(import.meta.env.DEV);
})();

/** 이 단계의 이미지가 "무엇"인지 — 원본인지 사본인지 명시 */
export type ImageOrigin =
  | "original-upload"
  | "normalized-copy"
  | "canvas-export"
  | "display-thumbnail"
  | "resized-blob"
  | "cutout-result"
  | "unknown";

export interface ImageTraceRow {
  stage: string;
  origin: ImageOrigin;
  filename: string | null;
  mime: string | null;
  bytes: number | null;
  width: number | null;
  height: number | null;
  note?: string;
}

const rows: ImageTraceRow[] = [];

function approxDataUrlBytes(dataUrl: string): number | null {
  const comma = dataUrl.indexOf(",");
  if (comma < 0) return null;
  const b64 = dataUrl.length - comma - 1;
  return Math.round((b64 * 3) / 4);
}

function mimeOfDataUrl(dataUrl: string): string | null {
  return dataUrl.match(/^data:([^;,]+)/)?.[1] ?? null;
}

async function measureBlob(blob: Blob): Promise<{ width: number | null; height: number | null }> {
  try {
    const bmp = await createImageBitmap(blob);
    const out = { width: bmp.width, height: bmp.height };
    bmp.close?.();
    return out;
  } catch {
    return { width: null, height: null };
  }
}

function measureDataUrl(src: string): Promise<{ width: number | null; height: number | null }> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => resolve({ width: img.naturalWidth, height: img.naturalHeight });
    img.onerror = () => resolve({ width: null, height: null });
    img.src = src;
  });
}

/**
 * 한 단계의 이미지를 측정해 콘솔에 남긴다. 절대 throw 하지 않는다
 * (계측이 파이프라인을 깨뜨리면 안 됨).
 */
export async function traceImage(
  stage: string,
  source: File | Blob | string | null | undefined,
  origin: ImageOrigin,
  note?: string
): Promise<ImageTraceRow | null> {
  if (!IMAGE_TRACE_ENABLED || !source) return null;
  try {
    let row: ImageTraceRow;

    if (typeof source === "string") {
      const isData = source.startsWith("data:");
      const dims = isData
        ? await measureDataUrl(source)
        : await measureDataUrl(source).catch(() => ({ width: null, height: null }));
      row = {
        stage,
        origin,
        filename: isData ? null : source.slice(0, 80),
        mime: isData ? mimeOfDataUrl(source) : null,
        bytes: isData ? approxDataUrlBytes(source) : null,
        width: dims.width,
        height: dims.height,
        note,
      };
    } else {
      const dims = await measureBlob(source);
      row = {
        stage,
        origin,
        filename: source instanceof File ? source.name : null,
        mime: source.type || null,
        bytes: source.size,
        width: dims.width,
        height: dims.height,
        note,
      };
    }

    rows.push(row);
    const px = row.width && row.height ? `${row.width}x${row.height}` : "?x?";
    const kb = row.bytes != null ? `${Math.round(row.bytes / 1024)}KB` : "?KB";
    // eslint-disable-next-line no-console
    console.log(
      `%c[IMAGE-TRACE]%c ${stage.padEnd(28)} ${px.padEnd(11)} ${kb.padEnd(8)} ` +
        `${(row.mime ?? "-").padEnd(12)} origin=${row.origin}` +
        (row.filename ? ` file=${row.filename}` : "") +
        (note ? `  // ${note}` : ""),
      "color:#c9a227;font-weight:bold",
      "color:inherit"
    );
    return row;
  } catch {
    return null;
  }
}

/** 지금까지 수집된 모든 단계를 표로 출력 (브라우저 콘솔에서 호출 가능). */
export function dumpImageTrace(): ImageTraceRow[] {
  if (!IMAGE_TRACE_ENABLED) return [];
  // eslint-disable-next-line no-console
  console.table(rows);
  return rows;
}

export function resetImageTrace(): void {
  rows.length = 0;
}

if (typeof window !== "undefined" && IMAGE_TRACE_ENABLED) {
  (window as unknown as Record<string, unknown>).__imageTrace = dumpImageTrace;
  (window as unknown as Record<string, unknown>).__imageTraceReset = resetImageTrace;
}
