/**
 * 원본 레퍼런스 영구 보존 (Durable Pet Identity Intake, Phase 1).
 *
 * 배경: 원본 사진은 지금까지 브라우저 상태(data: URL)에만 있었다. 서버 누끼조차
 * normalize 로 축소된 사본을 받으므로, 원본 해상도 증거는 어디에도 남지 않았다.
 * 여기서 **누끼 성공 직후** 원본 바이트를 그대로 POST /api/assets/original 로
 * 올린다 — 이후 신원 파이프라인(멀티뷰 → 정본 이미지)의 version 1 레퍼런스다.
 *
 * Phase 7B's authoritative `persistPhase1Intake` is authenticated, awaited,
 * observable, and retryable. The older fail-open helper remains below only for
 * callers that still need its legacy return contract.
 *
 * (순수 모듈 테스트를 위해 상대 경로 import 규칙을 따른다 — idle-generation-request.ts 참고)
 */

/** 병리적 업로드 가드. 서버(assets.py ORIGINAL_MAX_BYTES)와 같은 값. */
export const ORIGINAL_REFERENCE_MAX_BYTES = 40 * 1024 * 1024;

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** data: URL → 바이트 + MIME. data: 가 아니거나 못 읽으면 null. */
export function decodeDataUrl(
  dataUrl: string,
): { bytes: Uint8Array<ArrayBuffer>; mime: string } | null {
  const raw = (dataUrl || "").trim();
  if (!raw.toLowerCase().startsWith("data:")) return null;
  const comma = raw.indexOf(",");
  if (comma < 0) return null;

  const header = raw.slice(5, comma); // "image/jpeg;base64" 등
  const mime = (header.split(";")[0] || "").trim() || "application/octet-stream";
  const body = raw.slice(comma + 1);

  try {
    if (/;\s*base64/i.test(header)) {
      const bin = atob(body);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      return { bytes, mime };
    }
    return { bytes: new TextEncoder().encode(decodeURIComponent(body)), mime };
  } catch {
    return null;
  }
}

export type OriginalReferenceResult = {
  referenceId: string | null;
  version: number | null;
  recorded: boolean;
  deduplicated: boolean;
};

export type Phase1IntakeResult = OriginalReferenceResult & {
  userId: string;
  contentId: string;
  petId: string;
  objectPath: string;
  cutoutReferenceId: string | null;
  cutoutObjectPath: string | null;
  cutoutRecorded: boolean;
  intakeReady: boolean;
};

export class Phase1IntakeError extends Error {
  readonly status?: number;
  readonly code?: string;

  constructor(
    message: string,
    status?: number,
    code?: string,
  ) {
    super(message);
    this.name = "Phase1IntakeError";
    this.status = status;
    this.code = code;
  }
}

function originalForm(params: {
  userId: string;
  contentId: string;
  dataUrl: string;
  diagnostics?: unknown;
  cutoutFile?: File;
}): FormData | null {
  const userId = (params.userId || "").trim();
  const contentId = (params.contentId || "").trim();
  if (!userId || !contentId) return null;

  const decoded = decodeDataUrl(params.dataUrl);
  if (!decoded || decoded.bytes.length === 0) return null;
  if (decoded.bytes.length > ORIGINAL_REFERENCE_MAX_BYTES) return null;

  const ext = decoded.mime.split("/")[1] || "bin";
  const form = new FormData();
  form.append("file", new File([decoded.bytes], `original.${ext}`, { type: decoded.mime }));
  form.append("user_id", userId);
  form.append("content_id", contentId);
  if (params.cutoutFile) form.append("cutout_file", params.cutoutFile);
  if (params.diagnostics && typeof params.diagnostics === "object") {
    try {
      form.append("diagnostics_json", JSON.stringify(params.diagnostics));
    } catch {
      /* diagnostics are optional */
    }
  }
  return form;
}

async function errorFromResponse(res: Response): Promise<Phase1IntakeError> {
  try {
    const body = (await res.json()) as {
      detail?: string | { code?: string; message?: string };
    };
    const detail = body.detail;
    if (typeof detail === "object" && detail) {
      return new Phase1IntakeError(
        detail.message || `Phase 1 intake failed (${res.status})`,
        res.status,
        detail.code,
      );
    }
    return new Phase1IntakeError(
      typeof detail === "string" ? detail : `Phase 1 intake failed (${res.status})`,
      res.status,
    );
  } catch {
    return new Phase1IntakeError(`Phase 1 intake failed (${res.status})`, res.status);
  }
}

/**
 * Authoritative Phase 7B write. Unlike the legacy helper below, failures throw:
 * callers must not proceed until the original is durable and, when supplied,
 * the derived cutout is paired to it.
 */
export async function persistPhase1Intake(params: {
  userId: string;
  contentId: string;
  dataUrl: string;
  accessToken: string;
  diagnostics?: unknown;
  cutoutFile?: File;
}): Promise<Phase1IntakeResult> {
  const form = originalForm(params);
  if (!form) throw new Phase1IntakeError("Phase 1 intake input is invalid.");
  const token = (params.accessToken || "").trim();
  if (!token) throw new Phase1IntakeError("로그인이 필요합니다.", 401, "UNAUTHENTICATED");
  form.append("phase1_intake", "true");

  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/assets/original`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
  } catch (error) {
    throw new Phase1IntakeError(
      error instanceof Error ? error.message : "Phase 1 intake network failure.",
    );
  }
  if (!res.ok) throw await errorFromResponse(res);

  const body = (await res.json()) as Record<string, unknown>;
  return {
    userId: String(body.user_id || ""),
    contentId: String(body.content_id || ""),
    petId: String(body.pet_id || ""),
    referenceId: body.reference_id ? String(body.reference_id) : null,
    objectPath: String(body.object_path || ""),
    version: typeof body.version === "number" ? body.version : null,
    recorded: body.reference_recorded === true,
    deduplicated: body.deduplicated === true,
    cutoutReferenceId: body.cutout_reference_id
      ? String(body.cutout_reference_id)
      : null,
    cutoutObjectPath: body.cutout_object_path
      ? String(body.cutout_object_path)
      : null,
    cutoutRecorded: body.cutout_recorded === true,
    intakeReady: body.intake_ready === true,
  };
}

/**
 * 원본을 서버에 영구 보존한다. 실패해도 throw 하지 않는다 — null 을 돌려준다.
 *
 * 같은 바이트의 재호출은 서버가 멱등 처리한다(새 버전이 생기지 않는다).
 */
export async function persistOriginalReference(params: {
  userId: string;
  contentId: string;
  /** 업로드 화면이 만든 **원본** data: URL (normalize 이전). */
  dataUrl: string;
  /** 서버 누끼가 준 cutout_quality 메타 — 있으면 그대로 동봉한다. */
  diagnostics?: unknown;
}): Promise<OriginalReferenceResult | null> {
  try {
    const form = originalForm(params);
    if (!form) return null;

    const res = await fetch(`${apiBase()}/api/assets/original`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) return null;
    const body = (await res.json()) as {
      reference_id?: string | null;
      version?: number | null;
      reference_recorded?: boolean;
      deduplicated?: boolean;
    };
    return {
      referenceId: body.reference_id ?? null,
      version: body.version ?? null,
      recorded: body.reference_recorded !== false,
      deduplicated: body.deduplicated === true,
    };
  } catch {
    return null;
  }
}

/** 한 장의 인테이크가 끝나 **원본과 누끼가 서버에서 짝지어진** 결과. */
export type ReadyIntakePair = {
  petId: string;
  referenceId: string;
  cutoutReferenceId: string;
};

/**
 * 세션에 실리는 Phase 7B 영수증.
 *
 * 단수 필드는 기존 소비자를 위해 그대로 남는다(준비된 **첫** 쌍). 복수 필드가
 * 실제 계약이다 — 활성 펫에서 준비 완료된 모든 원본/누끼 쌍(1–3장)이다.
 */
export type Phase1IntakeReceipt = {
  status: "ready";
  pet_id: string;
  original_reference_id: string;
  cutout_reference_id: string;
  original_reference_ids: string[];
  cutout_reference_ids: string[];
};

/**
 * 준비 완료된 쌍들을 영수증 하나로 모은다. **순수 함수**.
 *
 * - 활성 pet_id 와 다른 쌍은 버린다 — 다른 펫 슬롯의 레퍼런스가 섞이면 안 된다.
 * - 원본/누끼 한쪽만 있는 것은 쌍이 아니므로 싣지 않는다.
 * - 같은 원본 id 재등장(서버 멱등 재업로드)은 한 번만 싣는다.
 * - 실을 것이 하나도 없으면 null — 빈 영수증을 만들지 않는다.
 */
export function buildPhase1IntakeReceipt(
  petId: string,
  ready: readonly ReadyIntakePair[],
): Phase1IntakeReceipt | null {
  const activePetId = (petId || "").trim();
  if (!activePetId) return null;

  const originals: string[] = [];
  const cutouts: string[] = [];
  for (const pair of ready || []) {
    if (!pair || (pair.petId || "").trim() !== activePetId) continue;
    const original = (pair.referenceId || "").trim();
    const cutout = (pair.cutoutReferenceId || "").trim();
    if (!original || !cutout) continue;
    if (originals.includes(original)) continue;
    originals.push(original);
    cutouts.push(cutout);
  }
  if (originals.length === 0) return null;

  return {
    status: "ready",
    pet_id: activePetId,
    original_reference_id: originals[0],
    cutout_reference_id: cutouts[0],
    original_reference_ids: originals,
    cutout_reference_ids: cutouts,
  };
}
