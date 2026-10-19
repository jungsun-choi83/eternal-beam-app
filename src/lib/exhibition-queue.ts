/**
 * 전시 대기열 — 경로 판정 · 순수 헬퍼 · API 클라이언트.
 *
 *   /exhibition/staff  스태프(운영자 인증) — 사진 접수 · 대기열 · SHOW NEXT   (ops-nav.ts)
 *   /exhibition/queue  공개 — NOW SHOWING / UP NEXT 만, 관리 기능 없음
 *
 * 둘 다 `?exhibition=<id>` 로 전시를 고른다 (없으면 서버 기본값).
 * 실시간 채널 없이 폴링한다 — 리포에 realtime 인프라가 없고, 대기열은 몇 초 지연이면 충분하다.
 *
 * ⚠️ 이 파일은 node --test 가 직접 부른다 — `@/` 임포트를 쓰지 않는다.
 */

export const EXHIBITION_QUEUE_PATH = "/exhibition/queue";
export const STAFF_POLL_MS = 4_000;
export const PUBLIC_POLL_MS = 5_000;

export interface QueueItem {
  run_id: string;
  queue_number: number;
  pet_name: string | null;
  created_at: string | null;
  display_status: "WAITING" | "UP_NEXT" | "NOW_SHOWING" | "COMPLETE";
  processing_status: "QUEUED" | "PROCESSING" | "READY" | "FAILED";
  handoff_status: string | null;
  detail: string | null;
}

export interface StaffQueue {
  exhibition_id: string;
  now_showing: QueueItem | null;
  up_next: QueueItem | null;
  ready: QueueItem[];
  preparing: QueueItem[];
  needs_attention: QueueItem[];
  recently_complete: QueueItem[];
  handoff_required: boolean;
}

export interface PublicQueueItem {
  queue_number: number;
  pet_name: string | null;
}

export interface PublicQueue {
  now_showing: PublicQueueItem | null;
  up_next: PublicQueueItem[];
}

export interface Submission {
  run_id: string;
  exhibition_id: string;
  queue_number: number;
  pet_name: string | null;
  created_at: string | null;
  display_status: string;
  processing_status: string;
}

// ── 순수 헬퍼 ────────────────────────────────────────────────────────────────

export function isExhibitionQueuePath(pathname: string): boolean {
  return ((pathname || "").replace(/\/+$/, "") || "/") === EXHIBITION_QUEUE_PATH;
}

export function isExhibitionQueueEntry(): boolean {
  if (typeof window === "undefined") return false;
  return isExhibitionQueuePath(window.location.pathname);
}

/** `?exhibition=` (또는 `exhibition_id`). 형식은 서버가 검사한다. */
export function readExhibitionId(search: string): string | null {
  const p = new URLSearchParams(search || "");
  const v = (p.get("exhibition") || p.get("exhibition_id") || "").trim();
  return v || null;
}

export function currentExhibitionId(): string | null {
  if (typeof window === "undefined") return null;
  return readExhibitionId(window.location.search);
}

/** 18 → "#018". 1000 이상은 그대로. */
export function formatQueueNumber(n: number): string {
  return `#${String(Math.max(0, Math.trunc(n))).padStart(3, "0")}`;
}

export function queueLabel(item: { queue_number: number; pet_name: string | null }): string {
  return item.pet_name ? `${formatQueueNumber(item.queue_number)} ${item.pet_name}` : formatQueueNumber(item.queue_number);
}

/** SHOW NEXT 를 누를 수 있는가 — 넘길 것이 하나라도 있어야 한다. */
export function canShowNext(q: StaffQueue | null): boolean {
  return Boolean(q && (q.now_showing || q.up_next));
}

/** SHOW NEXT 요청 본문 — 화면이 본 상태를 함께 보내 두 번 누름을 서버가 거절하게 한다. */
export function showNextBody(q: StaffQueue): Record<string, string | null> {
  return {
    exhibition_id: q.exhibition_id,
    expected_now_showing_run_id: q.now_showing?.run_id ?? null,
    expected_up_next_run_id: q.up_next?.run_id ?? null,
  };
}

// ── API ──────────────────────────────────────────────────────────────────────

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** OpsLayout 이 code 로 로그인/권한 화면을 고른다 (ops-production-api.ts 의 OpsError 와 같은 규칙). */
export class ExhibitionApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ExhibitionApiError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<ExhibitionApiError> {
  let code = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const b = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (b?.detail?.code) code = b.detail.code;
    if (b?.detail?.message) message = b.detail.message;
  } catch {
    /* 상태 코드로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  if (res.status === 403 && code === "UNKNOWN") code = "OPS_FORBIDDEN";
  return new ExhibitionApiError(code, message, res.status);
}

function withExhibition(path: string, exhibitionId: string | null): string {
  return exhibitionId ? `${path}?exhibition_id=${encodeURIComponent(exhibitionId)}` : path;
}

type FetchFn = typeof fetch;

export async function fetchStaffQueue(
  token: string,
  exhibitionId: string | null,
  fetchImpl: FetchFn = fetch
): Promise<StaffQueue> {
  const res = await fetchImpl(`${apiBase()}${withExhibition("/api/exhibition/staff/queue", exhibitionId)}`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as StaffQueue;
}

export async function submitPetPhoto(
  token: string,
  params: { file: Blob; petName: string; exhibitionId: string | null },
  fetchImpl: FetchFn = fetch
): Promise<Submission> {
  const form = new FormData();
  form.append("file", params.file, (params.file as File).name || "pet.jpg");
  if (params.petName.trim()) form.append("pet_name", params.petName.trim());
  if (params.exhibitionId) form.append("exhibition_id", params.exhibitionId);
  const res = await fetchImpl(`${apiBase()}/api/exhibition/staff/submissions`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as Submission;
}

export async function showNext(token: string, q: StaffQueue, fetchImpl: FetchFn = fetch): Promise<StaffQueue> {
  const res = await fetchImpl(`${apiBase()}/api/exhibition/staff/queue/show-next`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify(showNextBody(q)),
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as StaffQueue;
}

export async function fetchPublicQueue(exhibitionId: string | null, fetchImpl: FetchFn = fetch): Promise<PublicQueue> {
  const res = await fetchImpl(`${apiBase()}${withExhibition("/api/exhibition/queue", exhibitionId)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as PublicQueue;
}
