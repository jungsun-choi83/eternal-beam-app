/**
 * 운영 생산 콘솔 클라이언트 (Phase 13.1) — **내부 전용.**
 *
 * Phase 13 의 API 를 그대로 쓴다. 새 엔드포인트를 만들지 않는다.
 *
 * ⚠️ 파일 요청은 전부 Authorization 헤더를 요구하므로 `<img src>` / `<a href>` 로
 *    직접 가리킬 수 없다. blob 으로 받아서 objectURL 로 넘긴다 — 토큰을 쿼리에
 *    넣으면 브라우저 히스토리와 서버 로그에 운영 JWT 가 남는다.
 */

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

export interface OpsOrderRow {
  orderId: string;
  userId: string;
  petId: string;
  soulTraceLetterId: string | null;
  productType: string;
  amount: number;
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber: string | null;
  recipientName: string | null;
  addressLine1: string | null;
}

export interface OpsProductionState {
  orderId: string;
  userId: string;
  petId: string;
  soulTraceLetterId: string | null;
  productType: string;
  amount: number;
  paymentStatus: string;
  productionStatus: string;
  shippingStatus: string;
  trackingNumber: string | null;
  shakerShareId: string | null;
  packageReady: boolean;
  files: string[];
  recipientName: string | null;
  addressLine1: string | null;
}

export class OpsError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "OpsError";
    this.code = code;
    this.status = status;
  }
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
export function parseState(row: Record<string, unknown>): OpsProductionState {
  return {
    orderId: String(row.order_id ?? ""),
    userId: String(row.user_id ?? ""),
    petId: String(row.pet_id ?? ""),
    soulTraceLetterId:
      row.soul_trace_letter_id == null ? null : String(row.soul_trace_letter_id),
    productType: String(row.product_type ?? ""),
    amount: Number(row.amount ?? 0),
    paymentStatus: String(row.payment_status ?? ""),
    productionStatus: String(row.production_status ?? "pending"),
    shippingStatus: String(row.shipping_status ?? "pending"),
    trackingNumber: row.tracking_number == null ? null : String(row.tracking_number),
    shakerShareId: row.shaker_share_id == null ? null : String(row.shaker_share_id),
    packageReady: Boolean(row.package_ready),
    files: Array.isArray(row.files) ? row.files.map(String) : [],
    recipientName: row.recipient_name == null ? null : String(row.recipient_name),
    addressLine1: row.address_line1 == null ? null : String(row.address_line1),
  };
}

export function parseOrderRow(row: Record<string, unknown>): OpsOrderRow {
  const s = parseState(row);
  return {
    orderId: s.orderId, userId: s.userId, petId: s.petId,
    soulTraceLetterId: s.soulTraceLetterId, productType: s.productType,
    amount: s.amount, paymentStatus: s.paymentStatus,
    productionStatus: s.productionStatus, shippingStatus: s.shippingStatus,
    trackingNumber: s.trackingNumber, recipientName: s.recipientName,
    addressLine1: s.addressLine1,
  };
}

/** 결제된 주문 검색 (Phase 12 API 재사용). */
export async function searchPaidOrders(params: {
  query?: string | null;
  accessToken: string;
}): Promise<OpsOrderRow[]> {
  const qs = new URLSearchParams();
  if (params.query) qs.set("query", params.query);
  const res = await fetch(`${apiBase()}/api/v1/orders/ops/search?${qs}`, {
    cache: "no-store",
    headers: auth(params.accessToken),
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  const rows = Array.isArray(b.orders) ? b.orders : [];
  return rows.map((r) => parseOrderRow(r as Record<string, unknown>));
}

async function opsJson(
  path: string, token: string, init?: RequestInit
): Promise<OpsProductionState> {
  const res = await fetch(`${apiBase()}${path}`, {
    cache: "no-store",
    ...init,
    headers: { ...auth(token), ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw await readError(res);
  return parseState((await res.json()) as Record<string, unknown>);
}

export const fetchProductionState = (orderId: string, token: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}`, token);

export const prepareProduction = (
  orderId: string, token: string, body: { qrShareUrl?: string | null; photoImageUrl?: string | null }
) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/prepare`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      qr_share_url: body.qrShareUrl ?? undefined,
      photo_image_url: body.photoImageUrl ?? undefined,
    }),
  });

export const startProduction = (orderId: string, token: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/start`, token, { method: "POST" });

export const markProduced = (orderId: string, token: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/produced`, token, { method: "POST" });

export const markShipped = (orderId: string, token: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/ship`, token, { method: "POST" });

export const markDelivered = (orderId: string, token: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/delivered`, token, { method: "POST" });

export const addTracking = (orderId: string, token: string, trackingNumber: string) =>
  opsJson(`/api/v1/ops/production/${encodeURIComponent(orderId)}/tracking`, token, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tracking_number: trackingNumber }),
  });

/** 구성 파일 하나를 blob 으로. 미리보기와 내려받기가 같은 바이트다. */
export async function fetchProductionFile(params: {
  orderId: string;
  kind: string;
  accessToken: string;
}): Promise<Blob> {
  const res = await fetch(
    `${apiBase()}/api/v1/ops/production/${encodeURIComponent(params.orderId)}/file/${params.kind}`,
    { cache: "no-store", headers: auth(params.accessToken) }
  );
  if (!res.ok) throw await readError(res);
  return res.blob();
}

export async function fetchProductionZip(params: {
  orderId: string;
  accessToken: string;
}): Promise<Blob> {
  const res = await fetch(
    `${apiBase()}/api/v1/ops/production/${encodeURIComponent(params.orderId)}/download`,
    { cache: "no-store", headers: auth(params.accessToken) }
  );
  if (!res.ok) throw await readError(res);
  return res.blob();
}

/**
 * 보관된 QR 을 **다시** 내려받는다 (Phase 13.1).
 *
 * 토큰을 복원하지 않고, 새 공유도 만들지 않는다 — 이미 인쇄된 QR 과 같은 파일이다.
 */
export async function fetchShareQrAgain(params: {
  shareId: string;
  kind?: "svg" | "png";
  accessToken: string;
}): Promise<Blob> {
  const qs = new URLSearchParams({ kind: params.kind ?? "svg" });
  const res = await fetch(
    `${apiBase()}/api/v1/shaker/ops/share/${encodeURIComponent(params.shareId)}/qr?${qs}`,
    { cache: "no-store", headers: auth(params.accessToken) }
  );
  if (!res.ok) throw await readError(res);
  return res.blob();
}

/** blob → 파일 저장. 브라우저 인쇄가 아니라 **파일**을 넘긴다(인쇄소 입력). */
export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}
