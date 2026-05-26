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
  const healthUrl = `${base}/api/health`;

  try {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 3000);
    const res = await fetch(healthUrl, { method: "GET", signal: ctrl.signal });
    clearTimeout(tid);
    if (!res.ok) {
      markServerCutoutDisabled();
      return false;
    }
    return true;
  } catch {
    markServerCutoutDisabled();
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
