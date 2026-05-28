/**
 * 테스트 전용 — AI 누끼 없이 JPEG 리사이즈만 (플로우·IAP 테스트용)
 */
import { createDisplayImageUrl } from "@/lib/display-image";
import { normalizeImageToJpegFile } from "@/lib/normalize-image";

export async function mockCutoutFromFile(file: File): Promise<string> {
  const jpeg = await normalizeImageToJpegFile(file);
  const blob = jpeg;
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(blob);
  });
  return createDisplayImageUrl(dataUrl, 1024);
}
