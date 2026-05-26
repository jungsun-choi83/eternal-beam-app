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

export const MEDIA_FILE_ACCEPT = "image/*,video/*";
