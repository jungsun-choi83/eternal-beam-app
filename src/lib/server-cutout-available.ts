import { getVideoApiBaseUrl } from "@/app/services/videoProcessingApi";

const DISABLED_KEY = "eternal_beam_server_cutout_disabled";

/** Render 백엔드 없으면 404만 반복하지 않도록 기억 */
export async function isServerCutoutAvailable(): Promise<boolean> {
  if (import.meta.env.DEV) return true;
  try {
    if (sessionStorage.getItem(DISABLED_KEY) === "1") return false;
  } catch {
    /* ignore */
  }

  const base = getVideoApiBaseUrl();
  const healthUrl = base ? `${base}/api/health` : "/api/health";

  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 12_000);
    const res = await fetch(healthUrl, { method: "GET", signal: ctrl.signal });
    clearTimeout(tid);
    if (!res.ok) return false;
    return true;
  } catch {
    return false;
  }
}

export function markServerCutoutDisabled(): void {
  try {
    sessionStorage.setItem(DISABLED_KEY, "1");
  } catch {
    /* ignore */
  }
}

/** 이전에 Render 404/연결 불가였을 때만 서버 누끼 생략 (콜드스타트 1회 실패로는 생략하지 않음) */
export function isServerCutoutSkipped(): boolean {
  try {
    return sessionStorage.getItem(DISABLED_KEY) === "1";
  } catch {
    return false;
  }
}

export function clearServerCutoutSkipped(): void {
  try {
    sessionStorage.removeItem(DISABLED_KEY);
  } catch {
    /* ignore */
  }
}
