/**
 * 제휴처 운영 콘솔 클라이언트 (Phase 16) — **내부 전용.**
 *
 * 이 파일은 Soul Trace DB 를 알지 못한다. 알 수도 없다 — partners/partner_codes
 * 는 다른 Supabase 프로젝트에 있고, 그 service-role 키는 브라우저에 절대 오지
 * 않는다. 브라우저는 Eternal Beam 운영 API 만 부르고, 그 뒤는 서버가 S2S 로
 * 처리한다(backend/services/partner_admin.py).
 *
 * ⚠️ QR 이미지도 Authorization 헤더를 요구하므로 `<img src>` 로 직접 가리킬 수
 *    없다. blob 으로 받아 objectURL 로 넘긴다 — 토큰을 쿼리에 넣으면 브라우저
 *    히스토리와 서버 로그에 운영 JWT 가 남는다.
 */

import { OpsError } from "@/lib/ops-production-api";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** QR 이 고정하는 갈래. Soul Trace 의 LetterMode 와 **같은 낱말**이다. */
export type PartnerTrack = "living" | "memorial";
export type PartnerType = "HOSPITAL" | "FUNERAL";

export interface PartnerCodeRow {
  code: string;
  /** null = 갈래 없음. 고객이 첫 화면에서 직접 고른다(기존 동작). */
  track: PartnerTrack | null;
  active: boolean;
  createdAt: string | null;
}

export interface PartnerRow {
  partnerId: string;
  partnerType: PartnerType | string;
  partnerName: string;
  /** 0..1 (0.15 = 15%). */
  shareRate: number;
  active: boolean;
  createdAt: string | null;
  codes: PartnerCodeRow[];
}

/**
 * 공개 URL — QR 에 찍히는 바로 그 주소.
 *
 * 화면에 보여 주고 복사시키기 위한 것이다. **QR 이미지 자체는 서버가 만든다** —
 * 브라우저가 만든 주소로 인쇄하면 여기 오타 하나가 벽에 붙는다.
 */
export function partnerPublicUrl(code: string, base?: string): string {
  const origin = (base || "https://soultrace.eternalbeam.com").replace(/\/$/, "");
  return `${origin}/?p=${encodeURIComponent(code)}`;
}

async function readError(res: Response): Promise<OpsError> {
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
  return new OpsError(code, message, res.status);
}

function auth(t: string): Record<string, string> {
  return { Authorization: `Bearer ${t}` };
}

/** 순수 파서 — 테스트가 그대로 부른다. */
export function parseCode(row: Record<string, unknown>): PartnerCodeRow {
  const t = row.track == null ? null : String(row.track);
  return {
    code: String(row.code ?? ""),
    track: t === "living" || t === "memorial" ? t : null,
    active: Boolean(row.active),
    createdAt: row.created_at == null ? null : String(row.created_at),
  };
}

export function parsePartner(row: Record<string, unknown>): PartnerRow {
  const codes = Array.isArray(row.codes) ? row.codes : [];
  return {
    partnerId: String(row.partner_id ?? ""),
    partnerType: String(row.partner_type ?? ""),
    partnerName: String(row.partner_name ?? ""),
    shareRate: Number(row.share_rate ?? 0),
    active: Boolean(row.active),
    createdAt: row.created_at == null ? null : String(row.created_at),
    codes: codes.map((c) => parseCode(c as Record<string, unknown>)),
  };
}

async function json<T>(path: string, token: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    cache: "no-store",
    ...init,
    headers: { ...auth(token), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw await readError(res);
  return (await res.json()) as T;
}

export async function listPartners(token: string): Promise<PartnerRow[]> {
  const b = await json<Record<string, unknown>>("/api/v1/ops/partners", token);
  const rows = Array.isArray(b.partners) ? b.partners : [];
  return rows.map((r) => parsePartner(r as Record<string, unknown>));
}

export async function createPartner(
  token: string,
  body: {
    partnerName: string;
    partnerType: PartnerType;
    /** 0..1. 15% 는 0.15 다. */
    shareRate: number;
    active?: boolean;
    initialTrack?: PartnerTrack | null;
  },
): Promise<PartnerRow> {
  const row = await json<Record<string, unknown>>("/api/v1/ops/partners", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      partner_name: body.partnerName,
      partner_type: body.partnerType,
      share_rate: body.shareRate,
      active: body.active ?? true,
      initial_track: body.initialTrack ?? null,
    }),
  });
  return parsePartner(row);
}

export async function updatePartner(
  token: string,
  partnerId: string,
  patch: { active?: boolean; partnerName?: string; shareRate?: number },
): Promise<PartnerRow> {
  const row = await json<Record<string, unknown>>(
    `/api/v1/ops/partners/${encodeURIComponent(partnerId)}`,
    token,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active: patch.active,
        partner_name: patch.partnerName,
        share_rate: patch.shareRate,
      }),
    },
  );
  return parsePartner(row);
}

export async function issueCode(
  token: string,
  body: { partnerId: string; track: PartnerTrack | null },
): Promise<PartnerCodeRow> {
  const row = await json<Record<string, unknown>>("/api/v1/ops/partners/codes", token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ partner_id: body.partnerId, track: body.track }),
  });
  return parseCode(row);
}

export async function setCodeActive(
  token: string,
  code: string,
  active: boolean,
): Promise<PartnerCodeRow> {
  const row = await json<Record<string, unknown>>(
    `/api/v1/ops/partners/codes/${encodeURIComponent(code)}`,
    token,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active }),
    },
  );
  return parseCode(row);
}

/**
 * QR 이미지를 blob 으로 받는다. 호출부가 objectURL 을 만들어 미리보기·내려받기에
 * 함께 쓰고, 다 쓰면 revoke 한다.
 */
export async function fetchCodeQr(
  token: string,
  code: string,
  kind: "svg" | "png" = "png",
): Promise<Blob> {
  const res = await fetch(
    `${apiBase()}/api/v1/ops/partners/codes/${encodeURIComponent(code)}/qr?kind=${kind}`,
    { cache: "no-store", headers: auth(token) },
  );
  if (!res.ok) throw await readError(res);
  return await res.blob();
}
