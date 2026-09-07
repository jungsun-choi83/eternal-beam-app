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

export interface ReadyAsset {
  /** 호출 시점에 새로 서명된 URL — 저장하지 말고 그대로 재생에 쓴다. */
  url: string;
  /**
   * 명시 전달 포맷 (Phase 7I.1). "packed_alpha" = 새 시스템 vstack 파생물
   * (packed 렌더러 필수). null = 레거시 — 기존 규칙(blackkey/휴리스틱)이 맞다.
   */
  deliveryFormat: string | null;
}

export interface PremiumAssets {
  petId: string;
  /** 액션 id → 재생 가능한 URL */
  ready: Record<string, string>;
  /**
   * 액션 id → {새 서명 URL, 전달 포맷} (Phase 7I.1). ready 와 같은 키 집합.
   * 구서버(필드 없음)와 붙으면 ready 에서 파생되고 포맷은 null(레거시)이다.
   */
  readyAssets: Record<string, ReadyAsset>;
  generating: string[];
  missing: string[];
  /** 서버 레지스트리 그대로 — 프론트가 개수를 하드코딩하지 않게. */
  idleEvents: string[];
  actionEvents: string[];
  /**
   * 상품 키 → 크레딧 가격. **상품마다 다르다** (백엔드 digital_products).
   *
   * 예전에는 idleBundleCredits / actionEventCredits 두 스칼라였다. 그 모양 자체가
   * "카테고리가 가격을 정한다"는 전제를 담고 있어서, 아이들 이벤트 넷에 서로 다른
   * 값을 매길 방법이 없었다. 가격의 권위는 이제 서버 카탈로그 하나뿐이다.
   */
  prices: Record<string, number>;
  /**
   * 프리미엄 **생성**이 허용되는가 (구독 active 또는 해지 유예 기간).
   *
   * ⚠️ **재생 권한이 아니다.** ready 에 있는 자산은 entitled=false 여도 계속
   * 재생된다 — 구독이 만료돼도 이미 만든 모션과 설정은 남는다. BREATHING 은
   * 애초에 프리미엄이 아니라 이 값과 무관하게 언제나 돈다.
   */
  entitled: boolean;
  /**
   * 행동 id → ON/OFF. **등록된 프리미엄 행동 전체**가 들어온다(기본 켬).
   *
   * ⚠️ READY 와 별개 상태다 — 아직 만들지 않은 행동에도 값이 있다.
   * ⚠️ 아직 재생에 연결되지 않았다. 스케줄러는 이 값을 보지 않는다.
   */
  preferences: Record<string, boolean>;
  /** "active" | "canceled" | "expired" | null(구독 이력 없음) */
  subscriptionStatus: string | null;
  /** 서버에서 구독 게이트가 켜져 있는가. false 면 레거시 크레딧 과금 경로다. */
  subscriptionRequired: boolean;
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
  /** 구독이 없거나 만료됨 — 프리미엄 **생성**만 막힌다(재생은 계속된다). */
  | "SUBSCRIPTION_REQUIRED"
  /** 구독 상태를 읽지 못했다. fail-closed 라 생성은 일어나지 않았다. */
  | "SUBSCRIPTION_CHECK_UNAVAILABLE"
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
/**
 * ready_assets 파싱 — 구서버(필드 없음)에서는 ready 로 파생한다.
 *
 * **순수 함수**다: 어느 쪽으로 오든 호출부는 readyAssets 하나만 보면 되고,
 * 레거시 파생 항목의 포맷은 null(기존 규칙)이다.
 */
export function parseReadyAssets(
  raw: unknown,
  ready: Record<string, string>
): Record<string, ReadyAsset> {
  const out: Record<string, ReadyAsset> = {};
  if (raw && typeof raw === "object") {
    for (const [id, entry] of Object.entries(raw as Record<string, unknown>)) {
      const e = entry as { url?: unknown; delivery_format?: unknown };
      const url = typeof e?.url === "string" ? e.url.trim() : "";
      if (!url) continue;
      out[id] = {
        url,
        deliveryFormat:
          typeof e.delivery_format === "string" && e.delivery_format
            ? e.delivery_format
            : null,
      };
    }
  }
  for (const [id, url] of Object.entries(ready)) {
    if (!out[id] && typeof url === "string" && url) {
      out[id] = { url, deliveryFormat: null };
    }
  }
  return out;
}

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
  const ready = (b.ready as Record<string, string>) ?? {};
  return {
    petId: String(b.pet_id ?? params.petId),
    ready,
    readyAssets: parseReadyAssets(b.ready_assets, ready),
    generating: (b.generating as string[]) ?? [],
    missing: (b.missing as string[]) ?? [],
    idleEvents: (b.idle_events as string[]) ?? [],
    actionEvents: (b.action_events as string[]) ?? [],
    prices: (b.prices as Record<string, number>) ?? {},
    entitled: Boolean(b.entitled),
    preferences: (b.preferences as Record<string, boolean>) ?? {},
    subscriptionStatus:
      b.subscription_status == null ? null : String(b.subscription_status),
    // 구버전 서버(필드 없음)와 붙었을 때 "구독 불필요"로 착각하지 않도록 기본 true.
    subscriptionRequired: b.subscription_required == null
      ? true
      : Boolean(b.subscription_required),
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
 *
 * 계약은 kind + pet_id 뿐이다 (Phase 7H). 생성 입력(원본·누끼)은 서버가 pet_id 의
 * Phase 1 intake 기록에서 읽는다 — 브라우저의 data: URL 을 보낼 이유도, 원격 URL 로
 * 변환할 이유도 없다.
 */
export async function purchasePremium(params: {
  kind: string;
  petId: string;
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

/**
 * 행동 하나의 ON/OFF 저장. **생성을 일으키지 않는다.**
 *
 * 서버가 갱신된 **전체** 선호를 돌려주므로, 호출부는 응답 하나로 화면을 다시
 * 그릴 수 있다(낙관적 업데이트가 어긋나도 서버 값으로 수렴한다).
 */
export async function setBehaviorPreference(params: {
  petId: string;
  actionId: string;
  enabled: boolean;
  accessToken: string;
  signal?: AbortSignal;
}): Promise<Record<string, boolean>> {
  const res = await fetch(`${apiBase()}/api/v1/pet/premium/preference`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${params.accessToken}`,
    },
    body: JSON.stringify({
      pet_id: params.petId,
      action_id: params.actionId,
      enabled: params.enabled,
    }),
    signal: params.signal,
  });
  if (!res.ok) throw await readError(res);
  const b = (await res.json()) as Record<string, unknown>;
  return (b.preferences as Record<string, boolean>) ?? {};
}
