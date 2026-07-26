/** device.eternalbeam.com 등 실제 기기 테스트 호스트 — Vercel env 누락 시 안전한 기본값 */

export function isDeviceProductionHost(): boolean {
  if (typeof window === "undefined") return false;
  const host = window.location.hostname.toLowerCase();
  return host === "device.eternalbeam.com" || host.endsWith(".eternalbeam.com");
}

function envFlag(name: string): string {
  return String(import.meta.env[name] ?? "").trim();
}

/** VITE_CLIENT_CUTOUT=1 또는 device 호스트(기본 WASM 누끼) */
export function isClientCutoutFirst(): boolean {
  const v = envFlag("VITE_CLIENT_CUTOUT");
  if (v === "1") return true;
  if (v === "0") return false;
  return isDeviceProductionHost();
}

/** VITE_ENABLE_LUMA=1 또는 device 호스트(기본 idle API 호출) */
export function isLumaPipelineEnabled(): boolean {
  const v = envFlag("VITE_ENABLE_LUMA");
  if (v === "1") return true;
  if (v === "0") return false;
  return isDeviceProductionHost();
}

/** Vercel same-origin idle 폴백 — API 실패 시 항상 사용 */
export const SAME_ORIGIN_IDLE_FALLBACK_URL = "/demo/goya_idle_packed.mp4";

/** LUMA_MOCK·API 실패 시 데모 mp4 — device 호스트 same-origin 우선 */
export const DEFAULT_IDLE_TEST_FALLBACK_URL =
  "https://device.eternalbeam.com/demo/goya_idle_packed.mp4";

/** VITE_IDLE_TEST_FALLBACK=1 또는 device 호스트(테스트 중 항상 움직임 보장) */
export function isIdleTestFallbackEnabled(): boolean {
  const v = envFlag("VITE_IDLE_TEST_FALLBACK");
  if (v === "1") return true;
  if (v === "0") return false;
  return isDeviceProductionHost();
}

export function getIdleTestFallbackUrl(): string {
  const custom = envFlag("VITE_IDLE_TEST_FALLBACK_URL");
  if (custom) return custom;
  if (typeof window !== "undefined") return SAME_ORIGIN_IDLE_FALLBACK_URL;
  return DEFAULT_IDLE_TEST_FALLBACK_URL;
}

/** Goya 데모 idle mp4 — 사용자 cutout이 있으면 절대 사용하지 않음 */
export function isGoyaDemoIdleUrl(url: string | null | undefined): boolean {
  const u = String(url ?? "").trim().toLowerCase();
  if (!u) return false;
  return (
    u.includes("goya_idle") ||
    u.includes("goya_idle_packed") ||
    u.includes("/demo/goya")
  );
}

export type IdleDisplaySource =
  | { mode: "video"; src: string }
  | { mode: "cutout"; src: string };

/** idle mp4 — API URL 우선; 데모 Goya 목업은 cutout 있어도 표시 (session 저장만 제외) */
export function ensureIdleMp4Url(
  apiUrl: string | null | undefined,
  options?: { allowDemoFallback?: boolean; cutoutUrl?: string | null }
): string {
  const cutout = String(options?.cutoutUrl ?? "").trim();
  const u = String(apiUrl ?? "").trim();
  if (u) {
    const path = u.split("?")[0].split("#")[0].toLowerCase();
    const isVideo =
      path.endsWith(".mp4") ||
      path.endsWith(".webm") ||
      path.endsWith(".mov");
    if (isVideo) {
      if (cutout && isGoyaDemoIdleUrl(u)) {
        /* 사용자 cutout + API가 Goya mock URL → 아래 데모 폴백으로 */
      } else {
        return u;
      }
    }
  }
  const allowFallback = options?.allowDemoFallback ?? isIdleTestFallbackEnabled();
  if (allowFallback) return SAME_ORIGIN_IDLE_FALLBACK_URL;
  return "";
}

/** 미리보기·테마 선택 — idle mp4(데모 포함) 우선, 데모 꺼진 경우만 cutout 정적 */
export function resolveIdleDisplaySource(
  idleVideoUrl: string | null | undefined,
  cutoutUrl: string | null | undefined,
  options?: { allowDemoFallback?: boolean }
): IdleDisplaySource | null {
  const cutout = String(cutoutUrl ?? "").trim();
  const allowFallback = options?.allowDemoFallback ?? isIdleTestFallbackEnabled();
  const video = ensureIdleMp4Url(idleVideoUrl, {
    allowDemoFallback: allowFallback,
    cutoutUrl: cutout,
  });
  if (video) return { mode: "video", src: video };
  if (cutout && !allowFallback) return { mode: "cutout", src: cutout };
  return null;
}
