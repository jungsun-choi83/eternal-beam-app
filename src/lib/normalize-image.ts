/**
 * 누끼/ONNX가 읽을 수 있도록 사진을 JPEG로 정규화 (HEIC·webp·잘못된 MIME 대응).
 */
import { MOCK_CUTOUT_ENABLED, TEST_APP_MODE } from "@/lib/test-app-flags";

/** 누끼 업로드용 — 서버 CUTOUT_MAX_PIXEL(1280)과 맞춤 */
export const CUTOUT_UPLOAD_MAX_EDGE = 1280;

export async function normalizeImageToJpegFile(
  source: File | string,
  maxEdge = 2048,
  jpegQuality = 0.92
): Promise<File> {
  const blob = await loadAsDecodableBlob(source);
  const bitmap = await createImageBitmap(blob).catch(async () => {
    const url = URL.createObjectURL(blob);
    try {
      const img = await loadHtmlImage(url);
      return await imageElementToBitmap(img);
    } finally {
      URL.revokeObjectURL(url);
    }
  });

  const w = bitmap.width;
  const h = bitmap.height;
  if (!w || !h) {
    bitmap.close?.();
    throw new Error("사진 크기를 읽을 수 없습니다. 다른 사진을 선택해 주세요.");
  }

  const scale = maxEdge / Math.max(w, h);
  const cw = scale < 1 ? Math.max(1, Math.round(w * scale)) : w;
  const ch = scale < 1 ? Math.max(1, Math.round(h * scale)) : h;

  const canvas = document.createElement("canvas");
  canvas.width = cw;
  canvas.height = ch;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    bitmap.close?.();
    throw new Error("Canvas unavailable");
  }
  ctx.drawImage(bitmap, 0, 0, cw, ch);
  bitmap.close?.();

  const jpeg = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("사진 변환에 실패했습니다."))),
      "image/jpeg",
      jpegQuality
    );
  });

  return new File([jpeg], "upload.jpg", { type: "image/jpeg" });
}

export function normalizeImageForCutout(source: File | string): Promise<File> {
  return normalizeImageToJpegFile(source, CUTOUT_UPLOAD_MAX_EDGE, 0.88);
}

function dataUrlToBlob(dataUrl: string): Blob {
  const comma = dataUrl.indexOf(",");
  if (comma < 0) throw new Error("Invalid data URL");
  const header = dataUrl.slice(0, comma);
  const b64 = dataUrl.slice(comma + 1);
  const mime = header.match(/:(.*?);/)?.[1] || "image/jpeg";
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

async function loadAsDecodableBlob(source: File | string): Promise<Blob> {
  if (typeof source === "string") {
    if (source.startsWith("data:")) {
      return dataUrlToBlob(source);
    }
    if (source.startsWith("blob:")) {
      const res = await fetch(source);
      if (!res.ok) throw new Error("사진을 불러올 수 없습니다.");
      return res.blob();
    }
    const res = await fetch(source);
    if (!res.ok) throw new Error("사진을 불러올 수 없습니다.");
    return res.blob();
  }
  return source;
}

function loadHtmlImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () =>
      reject(
        new Error(
          "사진을 불러올 수 없습니다. 갤러리에서 JPG·PNG로 저장한 뒤 다시 선택해 주세요."
        )
      );
    img.decoding = "async";
    img.src = src;
  });
}

async function imageElementToBitmap(img: HTMLImageElement): Promise<ImageBitmap> {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas unavailable");
  ctx.drawImage(img, 0, 0);
  return createImageBitmap(canvas);
}

export function friendlyCutoutError(message: string, language = "ko"): string {
  const m = message.toLowerCase();
  if (m.includes("could not be decoded") || m.includes("decode")) {
    return language === "ko"
      ? "사진 형식을 읽지 못했습니다. JPG·PNG 사진으로 다시 선택해 주세요."
      : "Could not read this photo. Please choose a JPG or PNG image.";
  }
  if (
    m.includes("502") ||
    m.includes("503") ||
    m.includes("504") ||
    m.includes("bad gateway") ||
    m.includes("깨어나는 중")
  ) {
    return language === "ko"
      ? "누끼 서버가 깨어나는 중입니다. 1분 정도 기다린 뒤 ‘다시 시도’를 눌러 주세요."
      : "The cutout server is waking up. Wait about a minute, then tap Try again.";
  }
  if (
    m.includes("failed to fetch") ||
    m.includes("network") ||
    m.includes("load failed") ||
    m.includes("aborterror")
  ) {
    if (MOCK_CUTOUT_ENABLED) {
      return language === "ko"
        ? "테스트 모드 처리에 실패했습니다. 사진을 다시 선택해 주세요."
        : "Test mode processing failed. Please pick another photo.";
    }
    if (TEST_APP_MODE) {
      return language === "ko"
        ? "처리 서버가 준비 중입니다. Wi‑Fi에서 1분 뒤 다시 시도하거나, VITE_MOCK_CUTOUT=1 로 빠른 테스트를 켜 주세요."
        : "Server is waking up. Retry on Wi‑Fi in a minute, or enable VITE_MOCK_CUTOUT=1.";
    }
    return language === "ko"
      ? "처리 중 연결이 지연되었습니다. Wi‑Fi에서 잠시 후 다시 시도해 주세요."
      : "Connection delayed. Please retry on Wi‑Fi in a moment.";
  }
  if (m.includes("누끼 서버")) {
    return message;
  }
  return message;
}
