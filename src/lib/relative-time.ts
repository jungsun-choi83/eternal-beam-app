/** Shared "x ago" formatting for device/beam timestamps (KO/EN). */
export function formatRelative(iso: string | null, lang: "ko" | "en", never: string): string {
  if (!iso) return never;
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return never;
  const diffSec = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (diffSec < 10) return lang === "ko" ? "방금 전" : "just now";
  if (diffSec < 60) return lang === "ko" ? `${diffSec}초 전` : `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return lang === "ko" ? `${diffMin}분 전` : `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return lang === "ko" ? `${diffHr}시간 전` : `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return lang === "ko" ? `${diffDay}일 전` : `${diffDay}d ago`;
}
