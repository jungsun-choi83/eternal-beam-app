/**
 * 판매자/운영 Shaker API 클라이언트 — **내부 도구 전용.**
 *
 * 고객용 클라이언트(shaker-api.ts / shaker-share.ts)와 분리한 이유는 번들이다.
 * 공개 Shaker 화면은 QR 을 찍은 사람이 모바일 데이터로 여는 페이지라, 운영
 * 도구 코드가 딸려 갈 이유가 없다. 진입 자체가 App.tsx 에서 갈린다.
 *
 * 모든 호출이 인증을 요구하고, 서버가 다시 운영자 allowlist 로 거른다.
 */

import { getPremiumAccessToken } from "./premium-auth-token.ts";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export interface OpsPet {
  petId: string;
  ownerUserId: string;
  readyCount: number;
  source: "REGISTRY" | "LEGACY";
}

export interface OpsPetsResult {
  pets: OpsPet[];
  degraded: boolean;
  registryAvailable: boolean;
}

export interface OpsCreatedShare {
  shareId: string;
  petId: string;
  ownerUserId: string;
  /** ⚠️ 이 응답에서만 존재한다. 서버는 해시만 저장한다. */
  token: string;
  /** QR 에 인코딩되는 완전한 URL. */
  shareUrl: string;
  purpose: string;
}

export interface OpsShareSummary {
  shareId: string;
  petId: string;
  ownerUserId: string;
  petName: string | null;
  purpose: string | null;
  orderRef: string | null;
  createdBy: string | null;
  createdAt: string | null;
  revokedAt: string | null;
  active: boolean;
}

export class OpsApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "OpsApiError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<OpsApiError> {
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
  return new OpsApiError(code, message, res.status);
}

function auth(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

async function currentOpsToken(forceRefresh = false): Promise<string | null> {
  if (forceRefresh) {
    const { refreshAccessToken } = await import("./supabase-auth.ts");
    const refreshed = await refreshAccessToken();
    if (refreshed) return refreshed;
  }
  return (await getPremiumAccessToken()).token;
}

export interface OpsAuthFetchDeps {
  getToken?: () => Promise<string | null>;
  refreshToken?: () => Promise<string | null>;
  fetch?: typeof globalThis.fetch;
}

/** Every Ops request uses the current session and retries one 401 after refresh. */
export async function authenticatedOpsFetch(
  input: string,
  init: RequestInit = {},
  deps: OpsAuthFetchDeps = {}
): Promise<Response> {
  const getToken = deps.getToken ?? (() => currentOpsToken());
  const refreshToken = deps.refreshToken ?? (() => currentOpsToken(true));
  const sendFetch = deps.fetch ?? globalThis.fetch;
  let token = await getToken();
  if (!token) throw new OpsApiError("UNAUTHENTICATED", "로그인이 필요합니다.", 401);

  const send = (accessToken: string) =>
    sendFetch(input, {
      ...init,
      headers: { ...(init.headers || {}), ...auth(accessToken) },
    });
  let res = await send(token);
  if (res.status !== 401) return res;

  token = await refreshToken();
  if (!token) throw new OpsApiError("UNAUTHENTICATED", "세션을 복원하지 못했습니다.", 401);
  res = await send(token);
  return res;
}

/** 고객 펫 검색. **펫을 만들지 않는다** — 이미 있는 것을 찾을 뿐이다. */
export async function searchOpsPets(params: {
  query?: string | null;
  includeLegacy?: boolean;
  signal?: AbortSignal;
}): Promise<OpsPetsResult> {
  const qs = new URLSearchParams();
  if (params.query) qs.set("query", params.query);
  if (params.includeLegacy) qs.set("includeLegacy", "true");
  const res = await authenticatedOpsFetch(`${apiBase()}/api/v1/shaker/ops/pets?${qs}`, {
    cache: "no-store",
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.pets) ? b.pets : [];
  const pets = rows.map((r) => {
    const x = r as Record<string, unknown>;
    return {
      petId: String(x.pet_id ?? ""),
      ownerUserId: String(x.owner_user_id ?? ""),
      readyCount: Number(x.ready_count ?? 0),
      source: x.source === "LEGACY" ? "LEGACY" : "REGISTRY",
    };
  });
  return {
    pets,
    degraded: Boolean(b.degraded),
    registryAvailable: b.registry_available !== false,
  };
}

export function parseOpsShare(row: Record<string, unknown>): OpsShareSummary {
  return {
    shareId: String(row.share_id ?? ""),
    petId: String(row.pet_id ?? ""),
    ownerUserId: String(row.owner_user_id ?? ""),
    petName: row.pet_name == null ? null : String(row.pet_name).trim() || null,
    purpose: row.purpose == null ? null : String(row.purpose),
    orderRef: row.order_ref == null ? null : String(row.order_ref),
    createdBy: row.created_by == null ? null : String(row.created_by),
    createdAt: row.created_at == null ? null : String(row.created_at),
    revokedAt: row.revoked_at == null ? null : String(row.revoked_at),
    active: Boolean(row.active),
  };
}

export async function listOpsShares(params: {
  petId: string;
  signal?: AbortSignal;
}): Promise<OpsShareSummary[]> {
  const qs = new URLSearchParams({ pet_id: params.petId });
  const res = await authenticatedOpsFetch(`${apiBase()}/api/v1/shaker/ops/shares?${qs}`, {
    cache: "no-store",
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.shares) ? b.shares : [];
  return rows.map((r) => parseOpsShare(r as Record<string, unknown>));
}

/**
 * 공유 발급. 소유자와 BREATHING 위치를 **서버가 찾는다** — 운영자가 입력하지 않는다.
 * 오타 하나로 남의 펫에 QR 이 붙는 것을 구조적으로 막기 위해서다.
 */
export async function createOpsShare(params: {
  petId: string;
  petName?: string | null;
  purpose?: string;
  orderRef?: string | null;
  signal?: AbortSignal;
}): Promise<OpsCreatedShare> {
  const res = await authenticatedOpsFetch(`${apiBase()}/api/v1/shaker/ops/share`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      pet_id: params.petId,
      pet_name: params.petName ?? undefined,
      purpose: params.purpose ?? "OPS",
      order_ref: params.orderRef ?? undefined,
    }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    shareId: String(b.share_id ?? ""),
    petId: String(b.pet_id ?? params.petId),
    ownerUserId: String(b.owner_user_id ?? ""),
    token: String(b.token ?? ""),
    shareUrl: String(b.share_url ?? ""),
    purpose: String(b.purpose ?? "OPS"),
  };
}

export async function revokeOpsShare(params: {
  shareId: string;
  petId: string;
}): Promise<boolean> {
  const res = await authenticatedOpsFetch(
    `${apiBase()}/api/v1/shaker/ops/share/${encodeURIComponent(params.shareId)}/revoke`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pet_id: params.petId }),
    }
  );
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return Boolean(b.revoked);
}

/**
 * QR 을 blob 으로 받는다.
 *
 * `<img src>` 로 직접 가리킬 수 없는 이유: 이 엔드포인트는 Authorization 헤더를
 * 요구하는데 img 태그는 헤더를 붙이지 못한다. 토큰을 쿼리로 넘기는 방법도 있지만
 * 그러면 운영자 JWT 가 브라우저 히스토리·서버 로그에 남는다.
 */
export async function fetchOpsQr(params: {
  shareUrl: string;
  kind?: "svg" | "png";
  filename?: string;
}): Promise<Blob> {
  const qs = new URLSearchParams({
    share_url: params.shareUrl,
    kind: params.kind ?? "png",
    filename: params.filename ?? "shaker",
  });
  const res = await authenticatedOpsFetch(`${apiBase()}/api/v1/shaker/ops/qr?${qs}`, {
    cache: "no-store",
  });
  if (!res.ok) throw await readError(res);
  return res.blob();
}
