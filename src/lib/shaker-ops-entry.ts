/**
 * 운영 콘솔 진입 감지 — `/ops/shaker`.
 *
 * 고객 앱 바깥에 두는 이유는 두 가지다.
 *
 *   1. **번들** — 공개 Shaker 는 QR 을 찍은 사람이 모바일 데이터로 여는 페이지다.
 *      운영 도구 코드가 거기 딸려 갈 이유가 없다.
 *   2. **경계** — 운영은 판매자(Eternal Beam) 소유 영역이고 고객 앱은 고객이
 *      쓰는 영역이다. 진입을 나누면 그 경계가 코드 구조로 보인다.
 *
 * ⚠️ 이것은 **보안 경계가 아니다.** 경로를 아는 것만으로는 아무것도 못 한다 —
 * 인가는 서버가 한다(JWT + SHAKER_OPS_USER_IDS allowlist). 여기서 하는 일은
 * 어느 화면을 그릴지 정하는 것뿐이다.
 */

export const OPS_SHAKER_PATH = "/ops/shaker";

export function isOpsShakerPath(pathname: string): boolean {
  const path = (pathname || "").replace(/\/+$/, "") || "/";
  return path === OPS_SHAKER_PATH;
}

export function isOpsShakerEntry(): boolean {
  if (typeof window === "undefined") return false;
  return isOpsShakerPath(window.location.pathname);
}

/** 운영 화면이 그릴 수 있는 상태. */
export type OpsPhase =
  /** 토큰이 없다 — 운영자도 로그인해야 한다. */
  | "signed-out"
  /** 로그인은 했지만 allowlist 에 없다. */
  | "forbidden"
  | "ready"
  | "error";

export function deriveOpsPhase(input: {
  hasAuth: boolean;
  errorCode: string | null;
}): OpsPhase {
  if (!input.hasAuth) return "signed-out";
  if (input.errorCode === "OPS_FORBIDDEN") return "forbidden";
  if (input.errorCode === "UNAUTHENTICATED") return "signed-out";
  if (input.errorCode) return "error";
  return "ready";
}


/** 생산 콘솔 경로 (Phase 13.1). Shaker QR 콘솔과 형제다. */
export const OPS_PRODUCTION_PATH = "/ops/production";

export function isOpsProductionPath(pathname: string): boolean {
  const path = (pathname || "").replace(/\/+$/, "") || "/";
  return path === OPS_PRODUCTION_PATH;
}

export function isOpsProductionEntry(): boolean {
  if (typeof window === "undefined") return false;
  return isOpsProductionPath(window.location.pathname);
}
