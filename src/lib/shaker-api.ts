/**
 * 공개 Shaker API 클라이언트 — **조회 하나뿐이다.**
 *
 * premium-assets.ts 와의 결정적 차이: 이 모듈에는 **인증 헤더도 POST 도 없다.**
 * 로그인하지 않은 방문자가 쓰는 경로이고, 생성·구매 함수가 아예 존재하지 않아
 * 호출부가 실수로라도 과금 경로에 닿을 수 없다.
 *
 * 서버 계약: backend/routers/shaker_v1.py 의 ShakerPetResponse.
 * 그 모델에 있는 필드가 전부다 — 계정·지갑·구독·결제·주문·프로바이더는 없다.
 */

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 재생 가능한 액션 하나. */
export interface ShakerAction {
  id: string;
  url: string;
}

export interface ShakerPet {
  petId: string;
  petName: string | null;
  /** 무료 BREATHING 루프. 정책과 무관하게 언제나 온다. */
  breathingUrl: string;
  posterUrl: string | null;
  /**
   * 서버 정책이 허용한 READY 액션.
   *
   * ⚠️ 기본 정책(PM 미결)에서는 **빈 배열**이다. 프론트가 이 값으로 정책을 다시
   * 계산하지 않는다 — 서버가 이미 판정했고, 여기서 또 판정하면 두 곳이 어긋난다.
   */
  actions: ShakerAction[];
  /** 더블탭이 재생할 액션 id. null 이면 더블탭은 아무 일도 하지 않는다. */
  doubleTapActionId: string | null;
}

export type ShakerErrorCode =
  /** 없는 링크 · 형식 오류 · petId 불일치 — 전부 같은 답이다(탐색 힌트를 주지 않는다). */
  | "SHARE_NOT_FOUND"
  | "SHARE_REVOKED"
  | "SHARE_EXPIRED"
  | "SHARE_TOKEN_REQUIRED"
  /** 링크는 유효하지만 가리키는 자산을 찾을 수 없다. */
  | "SHARE_ASSET_UNAVAILABLE"
  | "RATE_LIMITED"
  /** 서버 장애 — 다시 시도할 가치가 있다. */
  | "SHARE_STORE_UNAVAILABLE"
  /** 네트워크가 끊겼다. 지하철·엘리베이터에서 흔하다. */
  | "NETWORK"
  | "UNKNOWN";

export class ShakerApiError extends Error {
  // 파라미터 프로퍼티를 쓰지 않는다 — node --test 의 타입 스트립 모드가 지원하지
  // 않아 테스트가 통째로 죽는다 (premium-assets.ts 와 같은 이유).
  readonly code: ShakerErrorCode;
  readonly status: number;
  /** 다시 시도해서 나아질 여지가 있는가 — 화면이 [다시 시도] 버튼을 정하는 기준. */
  readonly retryable: boolean;

  constructor(code: ShakerErrorCode, message: string, status: number) {
    super(message);
    this.name = "ShakerApiError";
    this.code = code;
    this.status = status;
    this.retryable =
      code === "NETWORK" || code === "RATE_LIMITED" || code === "SHARE_STORE_UNAVAILABLE";
  }
}

/**
 * HTTP 응답 → 에러 코드.
 *
 * 상태 코드를 먼저 보지 않고 본문의 code 를 우선한다 — 서버가 이미 의미를 정해
 * 보냈기 때문이다. 본문이 없을 때만 상태 코드로 추론한다.
 */
export function classifyShakerError(
  status: number,
  body: { detail?: { code?: string } } | null
): ShakerErrorCode {
  const code = body?.detail?.code;
  if (code) {
    const known: ShakerErrorCode[] = [
      "SHARE_NOT_FOUND",
      "SHARE_REVOKED",
      "SHARE_EXPIRED",
      "SHARE_TOKEN_REQUIRED",
      "SHARE_ASSET_UNAVAILABLE",
      "RATE_LIMITED",
      "SHARE_STORE_UNAVAILABLE",
    ];
    if ((known as string[]).includes(code)) return code as ShakerErrorCode;
  }
  if (status === 404) return "SHARE_NOT_FOUND";
  if (status === 410) return "SHARE_REVOKED";
  if (status === 429) return "RATE_LIMITED";
  if (status === 422) return "SHARE_TOKEN_REQUIRED";
  if (status >= 500) return "SHARE_STORE_UNAVAILABLE";
  return "UNKNOWN";
}

/**
 * 서버가 준 재생 URL 을 브라우저가 쓸 수 있는 형태로.
 *
 * 서버는 재생 URL 을 `/api/v1/shaker/asset?...` **상대 경로**로 준다 — 스토리지
 * 객체 경로에 고객 이메일이 들어 있어 서명 URL 을 그대로 실을 수 없기 때문이다.
 *
 * 그런데 프론트가 별도 API 도메인을 쓰는 배포(VITE_API_BASE_URL)에서는 상대 경로가
 * **웹 도메인**으로 해석돼 404 가 난다. 그래서 여기서 베이스를 붙인다. 같은
 * 오리진(Vercel 리라이트/Vite 프록시)이면 베이스가 비어 있어 그대로 상대 경로다.
 */
export function resolveAssetUrl(url: string, base: string = apiBase()): string {
  const v = (url || "").trim();
  if (!v) return v;
  // 절대 URL(프록시를 끈 설정)이면 그대로 쓴다.
  if (/^https?:\/\//i.test(v)) return v;
  if (!v.startsWith("/")) return v;
  return `${base}${v}`;
}

/** 서버 응답(snake_case) → 프론트 모델(camelCase). 순수 함수라 테스트가 그대로 부른다. */
export function parseShakerPet(body: Record<string, unknown>): ShakerPet {
  const rawActions = Array.isArray(body.actions) ? body.actions : [];
  const actions: ShakerAction[] = [];
  for (const a of rawActions) {
    const item = a as Record<string, unknown>;
    const id = String(item?.id ?? "").trim().toUpperCase();
    const url = resolveAssetUrl(String(item?.url ?? "").trim());
    // id 나 url 이 비면 재생할 수 없다. 조용히 버린다 — 화면이 "재생되지 않는
    // 버튼"을 그리는 것보다 없는 편이 낫다.
    if (id && url) actions.push({ id, url });
  }

  const declared = String(body.double_tap_action_id ?? "").trim().toUpperCase();
  // 서버가 지목한 액션이 actions 에 없으면 무시한다. 둘이 어긋나는 것은 서버 버그
  // 이지만, 그 결과가 "탭했는데 아무 일도 없음"이 되면 원인을 찾기 어렵다.
  const doubleTapActionId =
    declared && actions.some((a) => a.id === declared) ? declared : null;

  return {
    petId: String(body.pet_id ?? "").trim(),
    petName: body.pet_name == null ? null : String(body.pet_name).trim() || null,
    breathingUrl: resolveAssetUrl(String(body.breathing_url ?? "").trim()),
    posterUrl:
      body.poster_url == null
        ? null
        : resolveAssetUrl(String(body.poster_url).trim()) || null,
    actions,
    doubleTapActionId,
  };
}

/**
 * 공유 링크로 펫을 연다. **인증 없음, 생성 없음.**
 *
 * petId 를 함께 보내는 이유는 조회가 아니라 **대조**다 — 서버가 토큰이 데려온 펫과
 * 다르면 거절한다. 보내지 않아도 동작하지만, 보내면 링크가 잘못 조합된 경우를
 * 서버가 잡아 준다.
 */
export async function fetchShakerPet(params: {
  token: string;
  petId?: string | null;
  signal?: AbortSignal;
}): Promise<ShakerPet> {
  const qs = new URLSearchParams({ share: params.token });
  if (params.petId) qs.set("pet_id", params.petId);

  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/v1/shaker/pet?${qs}`, {
      method: "GET",
      cache: "no-store",
      signal: params.signal,
    });
  } catch (e) {
    // AbortError 는 화면 전환이지 장애가 아니다 — 그대로 던져 호출부가 무시하게 한다.
    if ((e as { name?: string })?.name === "AbortError") throw e;
    throw new ShakerApiError("NETWORK", "네트워크에 연결할 수 없습니다.", 0);
  }

  if (!res.ok) {
    let body: { detail?: { code?: string; message?: string } } | null = null;
    try {
      body = await res.json();
    } catch {
      /* 본문이 없어도 상태 코드로 충분하다 */
    }
    const code = classifyShakerError(res.status, body);
    throw new ShakerApiError(
      code,
      body?.detail?.message || `HTTP ${res.status}`,
      res.status
    );
  }

  const pet = parseShakerPet((await res.json()) as Record<string, unknown>);
  if (!pet.breathingUrl) {
    // BREATHING 이 없으면 Shaker 는 성립하지 않는다. 빈 화면 대신 명시적 상태를 준다.
    throw new ShakerApiError(
      "SHARE_ASSET_UNAVAILABLE",
      "이 펫의 영상을 불러올 수 없습니다.",
      res.status
    );
  }
  return pet;
}
