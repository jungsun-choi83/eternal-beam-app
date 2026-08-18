/**
 * 누끼/ONNX가 읽을 수 있도록 사진을 JPEG로 정규화 (HEIC·webp·잘못된 MIME 대응).
 */
import { CUTOUT_UPLOAD_MAX_EDGE_PX } from "@/lib/cutout-speed-mode";
import { MOCK_CUTOUT_ENABLED, TEST_APP_MODE } from "@/lib/test-app-flags";
import { traceImage } from "@/lib/image-trace"; // [IMAGE-TRACE]

/** 누끼 업로드용 — 서버 CUTOUT_MAX_PIXEL과 맞춤 (속도 모드 960) */
export const CUTOUT_UPLOAD_MAX_EDGE = CUTOUT_UPLOAD_MAX_EDGE_PX;

export async function normalizeImageToJpegFile(
  source: File | string,
  maxEdge = 2048,
  jpegQuality = 0.92
): Promise<File> {
  // [IMAGE-TRACE] 정규화 직전 — 여기 크기가 곧 "브라우저가 실제로 받은 원본"이다.
  await traceImage(
    "normalize:IN",
    typeof source === "string" ? source : source,
    "original-upload",
    `maxEdge=${maxEdge} q=${jpegQuality}`
  );

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

  const out = new File([jpeg], "upload.jpg", { type: "image/jpeg" });
  // [IMAGE-TRACE] 정규화 직후 — 캔버스 재인코딩 결과(원본 아님).
  await traceImage(
    "normalize:OUT",
    out,
    "canvas-export",
    `src=${w}x${h} -> canvas=${cw}x${ch} (maxEdge=${maxEdge})`
  );
  return out;
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

/**
 * 서버가 사진 자체를 거절했을 때(HTTP 422)의 안내 문구.
 * 코드는 backend/services/cutout_errors.py 와 1:1 대응.
 */
const CUTOUT_REJECTION_COPY: Record<string, { ko: string; en: string }> = {
  SUBJECT_NOT_DETECTED: {
    ko: "사진에서 반려동물을 찾지 못했습니다. 반려동물이 크게, 정면에 잘 보이는 사진으로 다시 시도해 주세요.",
    en: "We couldn't find a pet in this photo. Please use a photo where your pet is large and clearly visible.",
  },
  CUTOUT_MASK_TOO_SMALL: {
    ko: "반려동물이 사진에서 너무 작게 나왔습니다. 더 가까이 찍힌 사진을 사용해 주세요.",
    en: "Your pet is too small in this photo. Please use a closer shot.",
  },
  CUTOUT_MASK_TOO_LARGE: {
    ko: "배경과 반려동물을 구분하지 못했습니다. 배경이 단순한 사진으로 다시 시도해 주세요.",
    en: "We couldn't separate your pet from the background. Please try a photo with a simpler background.",
  },
  CUTOUT_ALPHA_EMPTY: {
    ko: "배경 제거 결과가 비어 있습니다. 다른 사진으로 다시 시도해 주세요.",
    en: "Background removal produced an empty image. Please try another photo.",
  },
  CUTOUT_RECTANGLE_LIKE: {
    ko: "반려동물의 윤곽을 제대로 따내지 못했습니다. 배경이 단순하고 반려동물이 잘 보이는 사진을 사용해 주세요.",
    en: "We couldn't trace your pet's outline. Please use a photo with a simple background where your pet is clearly visible.",
  },
};

/** 누끼 거절 코드 → 사용자 안내 문구. 알 수 없는 코드면 null. */
export function cutoutRejectionMessage(
  code: string,
  language = "ko"
): string | null {
  const copy = CUTOUT_REJECTION_COPY[code];
  if (!copy) return null;
  return language === "ko" ? copy.ko : copy.en;
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
  if (
    m.includes("시간 초과") ||
    m.includes("too long") ||
    m.includes("timed out") ||
    m.includes("timeout")
  ) {
    return language === "ko"
      ? "배경 제거가 너무 오래 걸립니다. Wi‑Fi에서 1~3분 기다린 뒤 ‘다시 시도’를 눌러 주세요."
      : "Background removal is taking too long. Wait 1–3 minutes on Wi‑Fi, then tap Try again.";
  }
  return message;
}

/**
 * 펫 영상(Luma) 생성 단계 전용 오류 문구.
 * friendlyCutoutError의 "서버가 깨어나는 중" 문구는 콜드스타트를 가정한 문구라
 * 누끼 완료 후 수 분간 Luma 생성을 돌리다 서버가 죽는 경우(OOM 등)에는 오해를 줌.
 * 이 경우 재시도해도 같은 원인이면 계속 실패할 수 있음을 알려줌.
 */
export function friendlyPetVideoError(message: string, language = "ko"): string {
  const m = message.toLowerCase();
  if (
    m.includes("failed to fetch") ||
    m.includes("network") ||
    m.includes("load failed") ||
    m.includes("aborterror") ||
    m.includes("502") ||
    m.includes("503") ||
    m.includes("504") ||
    m.includes("bad gateway")
  ) {
    return language === "ko"
      ? "영상 생성 중 서버에 문제가 생겼습니다. 사진 배경 제거는 이미 저장됐어요. 잠시 후 다시 시도해 주세요. 계속 같은 오류가 나오면 앱을 다시 실행한 뒤 시도해 주세요."
      : "The server ran into a problem while generating the pet video (your cutout is already saved). Please try again shortly. If this keeps happening, please restart the app and try again.";
  }
  return friendlyCutoutError(message, language);
}
