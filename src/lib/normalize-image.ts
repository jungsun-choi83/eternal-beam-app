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

async function loadAsDecodableBlob(source: File | string): Promise<Blob> {
  if (typeof source === "string") {
    if (source.startsWith("data:")) {
      const res = await fetch(source);
      return res.blob();
    }
    const res = await fetch(source);
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
  if (m.includes("failed to fetch") || m.includes("network")) {
    return language === "ko"
      ? "네트워크 오류입니다. 연결을 확인한 뒤 다시 시도해 주세요."
      : "Network error. Check your connection and try again.";
  }
  return message;
}
