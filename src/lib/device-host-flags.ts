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
