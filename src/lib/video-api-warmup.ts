/**
 * Render 콜드스타트 완화 — 누끼/결제 전 서버 깨우기
 */
import { getVideoApiBaseUrl } from "@/app/services/videoProcessingApi";

function healthUrl(): string {
  const base = getVideoApiBaseUrl();
  return base ? `${base}/api/health` : "/api/health";
}

export async function warmupVideoApi(options?: {
  retries?: number;
  timeoutMs?: number;
}): Promise<boolean> {
  const retries = options?.retries ?? 3;
  const timeoutMs = options?.timeoutMs ?? 12_000;
  const url = healthUrl();

  for (let i = 0; i < retries; i++) {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method: "GET", signal: ctrl.signal });
      clearTimeout(tid);
      if (res.ok) return true;
    } catch {
      clearTimeout(tid);
    }
    if (i < retries - 1) {
      await new Promise((r) => setTimeout(r, 1500 * (i + 1)));
    }
  }
  return false;
}
