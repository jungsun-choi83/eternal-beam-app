/**
 * 소유자용 Shaker 공유 링크 관리 — 발급 / 폐기 / 목록.
 *
 * 공개 화면(shaker-api.ts)과 **의도적으로 분리했다**. 그쪽은 인증이 없고 이쪽은
 * 인증이 필수다. 한 파일에 두면 공개 화면 번들에 인증 코드와 토큰 취득 경로가
 * 딸려 들어가고, 로그인하지 않은 방문자가 받는 자바스크립트에 그런 것이 있을
 * 이유가 없다.
 *
 * ⚠️ **원문 토큰은 발급 응답에서 단 한 번만 온다.** 서버는 해시만 저장하므로
 * 다시 조회할 수 없다. 호출부는 받은 즉시 QR 로 만들거나 사용자에게 보여 줘야
 * 하고, 잃어버리면 폐기하고 새로 발급하는 것이 유일한 경로다.
 */

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export interface CreatedShare {
  shareId: string;
  /** ⚠️ 이 응답에서만 존재한다. 다시 받을 수 없다. */
  token: string;
  /** QR 에 넣을 상대 경로 — `/shaker?petId=…&share=…` */
  sharePath: string;
  petId: string;
}

export interface ShareSummary {
  shareId: string;
  petId: string;
  petName: string | null;
  createdAt: string | null;
  revokedAt: string | null;
  expiresAt: string | null;
  /** 지금 이 링크가 열리는가 — 목록 UI 가 상태 배지를 그리는 기준. */
  active: boolean;
}

export class ShakerShareError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ShakerShareError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<ShakerShareError> {
  let code = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const body = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (body?.detail?.code) code = body.detail.code;
    if (body?.detail?.message) message = body.detail.message;
  } catch {
    /* 본문이 없어도 status 로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  return new ShakerShareError(code, message, res.status);
}

/** 절대 URL 조립 — QR 생성기에 넣을 값. */
export function absoluteShareUrl(sharePath: string, origin?: string): string {
  const base =
    (origin || (typeof window !== "undefined" ? window.location.origin : "")).replace(/\/$/, "");
  return `${base}${sharePath}`;
}

/** 서버 응답 → 목록 항목. 순수 함수라 테스트가 그대로 부른다. */
export function parseShareSummary(row: Record<string, unknown>): ShareSummary {
  const revokedAt = row.revoked_at == null ? null : String(row.revoked_at);
  const expiresAt = row.expires_at == null ? null : String(row.expires_at);
  // "지금 열리는가"의 판정은 서버가 resolve 시점에 다시 한다. 여기 값은 표시용이며,
  // 목록이 서버 판정과 다른 답을 내지 않도록 **같은 규칙**(폐기 또는 만료)을 쓴다.
  const expired = expiresAt ? Date.parse(expiresAt) <= Date.now() : false;
  return {
    shareId: String(row.share_id ?? ""),
    petId: String(row.pet_id ?? ""),
    petName: row.pet_name == null ? null : String(row.pet_name).trim() || null,
    createdAt: row.created_at == null ? null : String(row.created_at),
    revokedAt,
    expiresAt,
    active: !revokedAt && !expired,
  };
}

/**
 * 공유 링크 발급. **생성을 일으키지 않는다** — 이미 있는 URL 을 가리킬 뿐이다.
 *
 * breathingUrl 은 프론트가 이미 들고 있는 pipeline.idle_video_url 이다. 서버에
 * 펫 이름·BREATHING 저장소가 없어서 발급 시점 스냅샷으로 넘긴다.
 */
export async function createShakerShare(params: {
  petId: string;
  breathingUrl: string;
  petName?: string | null;
  posterUrl?: string | null;
  ttlDays?: number | null;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<CreatedShare> {
  const res = await fetch(`${apiBase()}/api/v1/shaker/share`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({
      pet_id: params.petId,
      breathing_url: params.breathingUrl,
      pet_name: params.petName ?? undefined,
      poster_url: params.posterUrl ?? undefined,
      ttl_days: params.ttlDays ?? undefined,
    }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    shareId: String(b.share_id ?? ""),
    token: String(b.token ?? ""),
    sharePath: String(b.share_path ?? ""),
    petId: String(b.pet_id ?? params.petId),
  };
}

/** 링크 폐기. 멱등 — 이미 폐기됐으면 revoked=false 로 돌아온다(오류가 아니다). */
export async function revokeShakerShare(params: {
  shareId: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<boolean> {
  const res = await fetch(
    `${apiBase()}/api/v1/shaker/share/${encodeURIComponent(params.shareId)}/revoke`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${params.accessToken}` },
      signal: params.signal,
    }
  );
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return Boolean(b.revoked);
}

/** 내 공유 링크 목록. **토큰은 오지 않는다** — 서버가 저장하지 않는다. */
export async function listShakerShares(params: {
  petId?: string | null;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<ShareSummary[]> {
  const qs = new URLSearchParams();
  if (params.petId) qs.set("pet_id", params.petId);
  const res = await fetch(`${apiBase()}/api/v1/shaker/shares?${qs}`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.shares) ? b.shares : [];
  return rows.map((r) => parseShareSummary(r as Record<string, unknown>));
}
