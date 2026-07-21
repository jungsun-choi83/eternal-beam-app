/**
 * 브라우저에서 배경 제거 — @imgly는 dynamic import (화면·타이머 먼저 살아 있게).
 */
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  normalizeImageForCutout,
  normalizeImageToJpegFile,
} from "@/lib/normalize-image";

/** 폰 WASM — 해상도 낮출수록 8분+ → 1~3분대 */
const CLIENT_CUTOUT_MAX_EDGE = 768;

/**
 * data URL → File. 브라우저 누끼(WASM) 결과를 서버 업로드용 File로 변환할 때 공용으로 사용.
 * (바이트 복사 버그 이력 있음 — charCodeAt(n)이어야 함. 두 번째 사본을 만들지 말고 이 함수를 공유할 것.)
 */
export function dataUrlToFile(dataUrl: string, filename = "cutout.png"): File {
  const arr = dataUrl.split(",");
  const mime = arr[0].match(/:(.*?);/)?.[1] || "image/jpeg";
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8arr = new Uint8Array(n);
  while (n--) u8arr[n] = bstr.charCodeAt(n);
  return new File([u8arr], filename, { type: mime });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read cutout blob"));
    reader.readAsDataURL(blob);
  });
}

function yieldToUi(ms = 80): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function clientCutoutFromFile(
  file: File,
  onStatus?: (line: string) => void,
  language = "ko"
): Promise<string> {
  const p = memorialT(language).processing;
  onStatus?.(p.statusLines[2]);
  await yieldToUi();
  const ready = await normalizeImageToJpegFile(
    file,
    CLIENT_CUTOUT_MAX_EDGE,
    0.85
  );
  await yieldToUi();

  onStatus?.(p.modelLoad);
  const { removeBackground } = await import("@imgly/background-removal");
  await yieldToUi();

  onStatus?.(p.steps[0].description);
  const blob = await removeBackground(ready, {
    // v1.5+는 Next.js 호환 문제로 proxyToWorker 기본값이 false로 바뀜 —
    // 이 앱은 Vite라 해당 없음. 명시적으로 켜서 무거운 추론을 Web Worker로
    // 넘기고 메인 스레드(타이머·화면 텍스트)가 멈춘 것처럼 보이지 않게 함.
    proxyToWorker: true,
    progress: (_key, current, total) => {
      if (total > 0 && current === 0) {
        onStatus?.(p.almostDone);
      }
    },
  });
  onStatus?.(p.cutoutDone);
  return blobToDataUrl(blob);
}

export async function clientCutoutFromDataUrl(
  dataUrl: string,
  onStatus?: (line: string) => void
): Promise<string> {
  const ready = await normalizeImageForCutout(dataUrl);
  return clientCutoutFromFile(ready, onStatus);
}
