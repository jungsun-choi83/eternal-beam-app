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

/** http(s) 절대 주소인가 — 확장자와 무관하게 "진짜 자산"인지 가리는 기준. */
function looksLikeRemoteAsset(url: string): boolean {
  return /^https?:\/\//i.test(url) || url.startsWith("blob:") || url.startsWith("/");
}

/**
 * idle mp4 — **실제 생성 자산이 있으면 그것이 이긴다.**
 *
 * ── 데모가 진짜 자산을 가리던 경로 ──────────────────────────────────────────
 * 예전에는 확장자(.mp4/.webm/.mov)로만 "영상인가"를 판정했다. 그런데 프로바이더와
 * 스토리지가 돌려주는 주소는 늘 확장자로 끝나지 않는다(서명 URL, CDN 리라이트,
 * 확장자 없는 오브젝트 키). 그러면 **방금 생성한 진짜 영상**이 isVideo=false 로
 * 떨어지고, 그 아래 폴백이 Goya 데모를 돌려줬다 — 사용자는 자기 아이 대신 남의
 * 강아지를 보게 된다.
 *
 * 배경이 구워진 뒤로는 이 오작동이 더 나쁘다: 데모 클립에는 고객이 승인한 배경도
 * 없으므로 화면이 통째로 다른 장면이 된다.
 *
 * 그래서 판정을 뒤집는다 — **Goya 목업이 아닌 원격 자산이면 무조건 그것을 쓴다.**
 * 데모는 자산이 아예 없을 때만 나온다.
 */
export function ensureIdleMp4Url(
  apiUrl: string | null | undefined,
  options?: { allowDemoFallback?: boolean; cutoutUrl?: string | null }
): string {
  const cutout = String(options?.cutoutUrl ?? "").trim();
  const u = String(apiUrl ?? "").trim();
  if (u && looksLikeRemoteAsset(u)) {
    // 사용자 cutout 이 있는데 API 가 Goya 목업을 준 경우에만 폴백으로 내려간다.
    if (!(cutout && isGoyaDemoIdleUrl(u))) return u;
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
