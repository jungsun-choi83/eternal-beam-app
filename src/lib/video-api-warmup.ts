/**
 * Render 콜드스타트 완화 — 누끼/결제 전 서버 깨우기
 */
import { getVideoApiBaseUrl } from "@/app/services/videoProcessingApi";

function healthUrl(): string {
  const base = getVideoApiBaseUrl();
  return base ? `${base}/api/health` : "/api/health";
}

/** coldStart 시 health 폴링 상한 (초과 시 누끼 요청으로 서버 깨우기 시도) */
const COLD_START_MAX_WAIT_MS = 50_000;

export async function warmupVideoApi(options?: {
  retries?: number;
  timeoutMs?: number;
  /** Render sleep·Vercel 502 — health 폴링 (상한 maxWaitMs) */
  coldStart?: boolean;
  /** coldStart일 때 전체 대기 상한(ms). 기본 50초 */
  maxWaitMs?: number;
  onAttempt?: (attempt: number, total: number) => void;
}): Promise<boolean> {
  const cold = options?.coldStart ?? false;
  const maxWaitMs = options?.maxWaitMs ?? (cold ? COLD_START_MAX_WAIT_MS : undefined);
  const retries = options?.retries ?? (cold ? 10 : 3);
  const timeoutMs = options?.timeoutMs ?? (cold ? 10_000 : 12_000);
  const url = healthUrl();
  const started = Date.now();

  for (let i = 0; i < retries; i++) {
    if (maxWaitMs != null && Date.now() - started >= maxWaitMs) {
      break;
    }
    options?.onAttempt?.(i + 1, retries);
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, { method: "GET", signal: ctrl.signal });
      clearTimeout(tid);
      if (res.ok) return true;
      if (cold && res.status >= 500 && i < retries - 1) {
        const wait = Math.min(4000 + i * 800, 8000);
        if (maxWaitMs != null && Date.now() - started + wait >= maxWaitMs) {
          break;
        }
        await sleep(wait);
        continue;
      }
    } catch {
      clearTimeout(tid);
    }
    if (i < retries - 1) {
      const wait = cold ? Math.min(3000 + i * 600, 6000) : 1500 * (i + 1);
      if (maxWaitMs != null && Date.now() - started + wait >= maxWaitMs) {
        break;
      }
      await sleep(wait);
    }
  }
  return false;
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}
