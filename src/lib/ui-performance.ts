/** 성능 우선 UI — 기본 on. 풀 이펙트는 VITE_FULL_UI=1 */
export function isLiteUI(): boolean {
  if (import.meta.env.VITE_FULL_UI === "1") return false;
  if (import.meta.env.VITE_LITE_UI === "0") return false;
  if (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return true;
  }
  return true;
}

export function applyUILiteClass(): void {
  if (typeof document === "undefined") return;
  document.documentElement.classList.toggle("ui-lite", isLiteUI());
}
