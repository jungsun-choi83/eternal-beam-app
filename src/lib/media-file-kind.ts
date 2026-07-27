export type MediaKind = "image" | "video";

/** Android 갤러리는 file.type 이 빈 문자열인 경우가 많음 */
export function inferMediaKind(file: File): MediaKind | null {
  const type = (file.type || "").toLowerCase();
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  const name = (file.name || "").toLowerCase();
  if (/\.(jpe?g|png|gif|webp|heic|heif|bmp)$/i.test(name)) return "image";
  if (/\.(mp4|webm|mov|m4v|3gp)$/i.test(name)) return "video";
  return null;
}

/** 갤러리만 — capture 미사용, image/* 는 모바일에서 카메라 옵션을 띄우는 경우가 있음 */
export const MEDIA_FILE_ACCEPT =
  "image/jpeg,image/png,image/webp,image/heic,image/heif,.jpg,.jpeg,.png,.webp,.heic,.heif,video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm";
