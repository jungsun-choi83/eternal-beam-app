/**
 * 누끼/ONNX가 읽을 수 있도록 사진을 JPEG로 정규화 (HEIC·webp·잘못된 MIME 대응).
 */
export async function normalizeImageToJpegFile(
  source: File | string,
  maxEdge = 2048
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
      0.92
    );
  });

  return new File([jpeg], "upload.jpg", { type: "image/jpeg" });
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
    m.includes("failed to fetch") ||
    m.includes("network") ||
    m.includes("load failed") ||
    m.includes("aborterror")
  ) {
    return language === "ko"
      ? "서버 연결이 느리거나 끊겼습니다. Wi‑Fi에서 다시 시도하거나, 1분 후 재시도해 주세요. (첫 실행은 AI 모델 다운로드로 시간이 걸릴 수 있습니다)"
      : "Connection failed or timed out. Try Wi‑Fi and retry in a minute.";
  }
  if (m.includes("누끼 서버")) {
    return message;
  }
  return message;
}
