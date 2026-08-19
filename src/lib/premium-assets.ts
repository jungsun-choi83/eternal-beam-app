/**
 * 프로덕션 프리미엄 API 클라이언트 — **발견과 구매를 엄격히 분리한다**.
 *
 * 이 분리가 이 모듈의 존재 이유다. 예전 경로(idle-event-dev-trigger / come-closer-autogen)
 * 는 "자산이 없으면 곧바로 생성 제출" 이었다. 개발용 무과금 엔드포인트에서는 편의였지만,
 * 유료 모델에서는 **화면을 열었다는 이유로 결제가 일어나는** 것과 같다.
 *
 *   discoverPremiumAssets()  GET  — 조회만. 절대 생성하지도 과금하지도 않는다.
 *   purchasePremium()        POST — 사용자의 명시적 구매 의사가 있을 때만 부른다.
 *
 * 가격(서버가 최종 권위, 응답으로 확인 가능):
 *   IDLE_BUNDLE        1 크레딧 — 등록된 아이들 이벤트 **전체**
 *   ACTION:<ACTION_ID> 1 크레딧 — 액션 1건
 */

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 아이들 번들 — 등록된 아이들 이벤트 전체를 1 크레딧에. */
export const KIND_IDLE_BUNDLE = "IDLE_BUNDLE";

/** 액션 이벤트 1건. `actionKind("COME_CLOSER")` */
export function actionKind(actionId: string): string {
  return `ACTION:${(actionId || "").trim().toUpperCase()}`;
}

export interface PremiumAssets {
  petId: string;
  /** 액션 id → 재생 가능한 URL */
  ready: Record<string, string>;
  generating: string[];
  missing: string[];
  /** 서버 레지스트리 그대로 — 프론트가 개수를 하드코딩하지 않게. */
  idleEvents: string[];
  actionEvents: string[];
  idleBundleCredits: number;
  actionEventCredits: number;
}

export interface PurchaseOutcome {
  kind: string;
  status: "ready" | "processing";
  /** **이번 호출이 실제로 차감한 크레딧.** 멱등 호출이면 0. */
  creditsCharged: number;
  creditsRemaining: number | null;
  ready: Record<string, string>;
  generating: string[];
  submitted: string[];
  alreadyOwned: boolean;
}

export type PremiumErrorCode =
  | "UNAUTHENTICATED"
  | "PET_NOT_OWNED"
  | "INSUFFICIENT_CREDITS"
  | "WALLET_UNAVAILABLE"
  | "GENERATION_SUBMIT_FAILED"
  | "UNKNOWN";

export class PremiumApiError extends Error {
  // 파라미터 프로퍼티(`constructor(readonly code: ...)`)를 쓰지 않는다 —
  // node --test 의 타입 스트립 모드가 지원하지 않아 테스트가 통째로 죽는다.
  readonly code: PremiumErrorCode;
  readonly status: number;

  constructor(code: PremiumErrorCode, message: string, status: number) {
    super(message);
    this.name = "PremiumApiError";
    this.code = code;
    this.status = status;
  }
}

async function readError(res: Response): Promise<PremiumApiError> {
  let code: PremiumErrorCode = "UNKNOWN";
  let message = `HTTP ${res.status}`;
  try {
    const body = (await res.json()) as { detail?: { code?: string; message?: string } };
    if (body?.detail?.code) code = body.detail.code as PremiumErrorCode;
    if (body?.detail?.message) message = body.detail.message;
  } catch {
    /* 본문이 없어도 status 로 충분하다 */
  }
  if (res.status === 401) code = "UNAUTHENTICATED";
  return new PremiumApiError(code, message, res.status);
}

/**
 * 이 펫의 프리미엄 자산 상태.
 *
 * **읽기 전용이다.** 자산이 없다고 해서 이 호출이 생성을 시작하지 않는다 —
 * 그러려면 purchasePremium() 으로 명시적 의사가 있어야 한다.
 */
export async function discoverPremiumAssets(params: {
  petId: string;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<PremiumAssets> {
  const qs = new URLSearchParams({ pet_id: params.petId });
  const res = await fetch(`${apiBase()}/api/v1/pet/premium/assets?${qs}`, {
    method: "GET",
    cache: "no-store",
    headers: { Authorization: `Bearer ${params.accessToken}` },
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    petId: String(b.pet_id ?? params.petId),
    ready: (b.ready as Record<string, string>) ?? {},
    generating: (b.generating as string[]) ?? [],
    missing: (b.missing as string[]) ?? [],
    idleEvents: (b.idle_events as string[]) ?? [],
    actionEvents: (b.action_events as string[]) ?? [],
    idleBundleCredits: Number(b.idle_bundle_credits ?? 1),
    actionEventCredits: Number(b.action_event_credits ?? 1),
  };
}

/**
 * 명시적 구매. **크레딧이 나가는 유일한 프론트 경로다.**
 *
 * 사용자 조작(구매 버튼 등)에서만 부른다. effect / 마운트 / 폴링에서 부르면
 * 안 된다 — 그러면 예전의 자동 결제 문제가 그대로 돌아온다.
 *
 * 멱등성은 서버가 쥔다(구매 원장의 부분 unique 인덱스). 두 번 눌러도, 탭이
 * 두 개여도, 새로고침해도 두 번 과금되지 않는다 — creditsCharged 로 확인할 수 있다.
 */
export async function purchasePremium(params: {
  kind: string;
  petId: string;
  petImageUrl?: string | null;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<PurchaseOutcome> {
  const res = await fetch(`${apiBase()}/api/v1/pet/premium/purchase`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({
      kind: params.kind,
      pet_id: params.petId,
      pet_image_url: params.petImageUrl ?? undefined,
    }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return {
    kind: String(b.kind ?? params.kind),
    status: (b.status as "ready" | "processing") ?? "processing",
    creditsCharged: Number(b.credits_charged ?? 0),
    creditsRemaining:
      b.credits_remaining == null ? null : Number(b.credits_remaining),
    ready: (b.ready as Record<string, string>) ?? {},
    generating: (b.generating as string[]) ?? [],
    submitted: (b.submitted as string[]) ?? [],
    alreadyOwned: Boolean(b.already_owned),
  };
}
