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
  /** Render sleep·Vercel 502 — 최대 ~2분까지 health 폴링 */
  coldStart?: boolean;
  onAttempt?: (attempt: number, total: number) => void;
}): Promise<boolean> {
  const cold = options?.coldStart ?? false;
  const retries = options?.retries ?? (cold ? 15 : 3);
  const timeoutMs = options?.timeoutMs ?? (cold ? 25_000 : 12_000);
  const url = healthUrl();

  for (let i = 0; i < retries; i++) {
    options?.onAttempt?.(i + 1, retries);
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method: "GET", signal: ctrl.signal });
      clearTimeout(tid);
      if (res.ok) return true;
      if (cold && res.status >= 500 && i < retries - 1) {
        await sleep(4000 + i * 1000);
        continue;
      }
    } catch {
      clearTimeout(tid);
    }
    if (i < retries - 1) {
      await sleep(cold ? 4000 + i * 1000 : 1500 * (i + 1));
    }
  }
  return false;
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
