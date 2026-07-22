/** Signed URLs (Supabase 등)는 `?token=` 쿼리가 붙어 `.endsWith('.mp4')`가 실패함 */
export function isLikelyVideoUrl(url: string | null | undefined): boolean {
  if (!url) return false;
  const u = url.toLowerCase().trim();
  if (u.startsWith("blob:")) return true;
  const path = u.split("?")[0].split("#")[0];
  return (
    path.endsWith(".mp4") ||
    path.endsWith(".webm") ||
    path.endsWith(".mov")
  );
}
