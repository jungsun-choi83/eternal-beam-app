/**
 * QR Shaker 진입 감지 — `/shaker?petId=<PET_ID>&share=<TOKEN>`.
 *
 * app-entry.ts 의 isPublicForestEntry / billingReturnEntry 와 같은 역할이고 같은
 * 자리에 둘 수도 있었지만, 이쪽은 **순수 파서를 테스트로 덮어야** 해서 분리했다.
 * app-entry.ts 의 함수들은 window 를 직접 읽어 node --test 에서 부를 수 없다.
 * 여기서는 파싱(readShakerParams)과 감지(isShakerEntry)를 나눠, 파싱만 순수하게 둔다.
 *
 * ⚠️ 이 모듈은 **인가하지 않는다.** 토큰 형식이 그럴듯한지만 본다. 진짜 판정은
 * 서버가 한다(해시 조회 + 폐기/만료 검사). 여기서 거르는 것은 오타나 잘린 QR 을
 * 네트워크 왕복 없이 즉시 알려 주기 위한 것뿐이다 — 보안 경계가 아니다.
 */

/** 서버 발급 토큰의 문자 집합 (base64url). backend/services/shaker_share.py 와 같다. */
const TOKEN_PATTERN = /^[A-Za-z0-9_-]+$/;

/** 서버의 TOKEN_MIN_LEN / TOKEN_MAX_LEN 과 같은 값. */
const TOKEN_MIN_LEN = 20;
const TOKEN_MAX_LEN = 128;

export const SHAKER_PATH = "/shaker";

export interface ShakerParams {
  /** URL 의 petId. **조회키가 아니다** — 서버는 토큰으로 펫을 찾고 이 값은 대조만 한다. */
  petId: string | null;
  /** 공유 토큰. 이것이 유일한 열쇠다. */
  token: string | null;
}

/**
 * 토큰 형식 검사. 서버 규칙과 **같은 값**을 쓴다.
 *
 * 형식이 틀린 것을 여기서 걸러도 보안이 좋아지지는 않는다(서버가 어차지 막는다).
 * 얻는 것은 UX 다: 잘린 QR 이나 복사 실수를 즉시 "링크가 올바르지 않습니다"로
 * 보여 줄 수 있고, 무의미한 요청이 레이트 리밋을 갉아먹지 않는다.
 */
export function isPlausibleShareToken(raw: string | null | undefined): boolean {
  const t = (raw || "").trim();
  if (t.length < TOKEN_MIN_LEN || t.length > TOKEN_MAX_LEN) return false;
  return TOKEN_PATTERN.test(t);
}

/**
 * 쿼리 문자열 → Shaker 파라미터. **순수 함수다.**
 *
 * `petId` 와 `pet_id` 를 모두 받는다. QR 은 인쇄되면 고칠 수 없는데, 생성 도구가
 * 어느 표기를 쓸지 지금 확정돼 있지 않다. 둘 다 받는 비용은 한 줄이고, 틀린 표기로
 * 찍힌 QR 을 회수하는 비용은 그렇지 않다.
 */
export function readShakerParams(search: string): ShakerParams {
  const p = new URLSearchParams(search || "");
  const petId = (p.get("petId") || p.get("pet_id") || "").trim();
  const token = (p.get("share") || "").trim();
  return {
    petId: petId || null,
    token: token || null,
  };
}

/** 이 경로가 Shaker 인가. 쿼리는 보지 않는다 — 토큰 유무는 화면이 판단한다. */
export function isShakerPath(pathname: string): boolean {
  const path = (pathname || "").replace(/\/+$/, "") || "/";
  return path === SHAKER_PATH;
}

/**
 * 지금 이 브라우저가 Shaker 로 열렸는가.
 *
 * 토큰이 없거나 형식이 틀려도 **true 다.** 그래야 Shaker 화면이 "이 링크는
 * 유효하지 않습니다"를 자기 자리에서 보여 줄 수 있다. 여기서 false 를 주면
 * 사용자는 이유 없이 메인 앱(QR 연결 화면)으로 떨어진다 — 무슨 일이 일어났는지
 * 알 수 없는 최악의 실패다.
 */
export function isShakerEntry(): boolean {
  if (typeof window === "undefined") return false;
  return isShakerPath(window.location.pathname);
}

/** 현재 URL 의 파라미터. 화면이 마운트될 때 한 번 읽는다. */
export function currentShakerParams(): ShakerParams {
  if (typeof window === "undefined") return { petId: null, token: null };
  return readShakerParams(window.location.search);
}

/**
 * 화면이 무엇을 그려야 하는가 — 네트워크 이전 단계의 판정.
 *
 * 요청을 보내기 전에 결정할 수 있는 것을 여기서 끝낸다. 화면은 이 결과만 보고
 * 분기하므로 "토큰 없음"과 "서버가 거절함"을 섞어 다루지 않는다.
 */
export type ShakerEntryState =
  | { kind: "ready"; token: string; petId: string | null }
  /** 링크에 토큰이 아예 없다 — QR 이 아니라 주소를 직접 친 경우가 대부분이다. */
  | { kind: "missing-token" }
  /** 토큰이 있지만 형식이 틀렸다 — 잘린 QR / 복사 실수. */
  | { kind: "malformed-token" };

export function resolveShakerEntry(params: ShakerParams): ShakerEntryState {
  if (!params.token) return { kind: "missing-token" };
  if (!isPlausibleShareToken(params.token)) return { kind: "malformed-token" };
  return { kind: "ready", token: params.token, petId: params.petId };
}
