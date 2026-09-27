/**
 * 인증 폼의 순수 규칙 — 검증 + 서버 오류 분류.
 *
 * 화면(auth-screen.tsx)과 Supabase 어댑터(supabase-auth.ts)가 같은 판정을 쓴다.
 * 여기에는 DOM 도, `@/` 별칭 import 도 없다 — node --test 가 그대로 실행한다.
 *
 * ── 가입 계약 ────────────────────────────────────────────────────────────────
 *   Sign Up : 이름 · 이메일 · 비밀번호   (세 칸이 전부다)
 *   Sign In : 이메일 · 비밀번호
 *
 * 예전 가입 폼이 요구하던 반려/기기 호출 이름은 **계정 만들기와 무관**하다.
 * 그 값은 펫 온보딩(사진 업로드) / My Beam 기기 설정 쪽 관심사다. 이 모듈에는
 * 그 필드가 없고, 숨은 요구도 없다.
 */

export type AuthMode = "login" | "signup";

export type AuthFieldKey = "name" | "email" | "password" | "confirm";

export type AuthFieldErrorCode =
  | "name_required"
  | "email_required"
  | "email_invalid"
  | "password_required"
  | "password_too_short"
  | "confirm_required"
  | "password_mismatch";

export type AuthFieldErrors = Partial<Record<AuthFieldKey, AuthFieldErrorCode>>;

export interface SignupInput {
  name: string;
  email: string;
  password: string;
}

export interface LoginInput {
  email: string;
  password: string;
}

/** 비밀번호 재설정 — 새 비밀번호 · 확인. 이메일도 반려 이름도 여기 없다. */
export interface NewPasswordInput {
  password: string;
  confirm: string;
}

/**
 * Supabase Auth 프로젝트 기본 최소 길이(6). 서버가 최종 판정한다 — 여기서는
 * 확실히 거절될 값을 왕복 없이 막고, 더 강한 규칙은 만들지 않는다.
 * (대시보드에서 더 길게 올리면 서버 오류가 weak_password 로 분류돼 그대로 보인다.)
 */
export const MIN_PASSWORD_LENGTH = 6;

/** 넉넉한 이메일 형태 검사 — 서버가 다시 검증하므로 RFC 전체를 흉내내지 않는다. */
export function isValidEmail(raw: string): boolean {
  const value = raw.trim();
  if (!value || value.length > 254) return false;
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(value);
}

export function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

export function validateSignup(input: SignupInput): AuthFieldErrors {
  const errors: AuthFieldErrors = {};
  if (!input.name.trim()) errors.name = "name_required";
  const email = input.email.trim();
  if (!email) errors.email = "email_required";
  else if (!isValidEmail(email)) errors.email = "email_invalid";
  if (!input.password) errors.password = "password_required";
  else if (input.password.length < MIN_PASSWORD_LENGTH) errors.password = "password_too_short";
  return errors;
}

export function validateLogin(input: LoginInput): AuthFieldErrors {
  const errors: AuthFieldErrors = {};
  const email = input.email.trim();
  if (!email) errors.email = "email_required";
  else if (!isValidEmail(email)) errors.email = "email_invalid";
  if (!input.password) errors.password = "password_required";
  return errors;
}

/**
 * 새 비밀번호 검증 — 재설정 화면 전용.
 *
 * 확인란은 **일치 여부**만 본다: 새 비밀번호 자체가 이미 틀렸으면(비었거나
 * 너무 짧으면) 그 오류만 보여 주고, 확인란에는 "먼저 위 칸을 고치라"는 뜻으로
 * 불일치를 겹쳐 띄우지 않는다.
 */
export function validateNewPassword(input: NewPasswordInput): AuthFieldErrors {
  const errors: AuthFieldErrors = {};
  if (!input.password) errors.password = "password_required";
  else if (input.password.length < MIN_PASSWORD_LENGTH) errors.password = "password_too_short";
  if (!input.confirm) errors.confirm = "confirm_required";
  else if (!errors.password && input.confirm !== input.password) errors.confirm = "password_mismatch";
  return errors;
}

export function hasFieldErrors(errors: AuthFieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

/** 서버/네트워크 실패를 화면 문구 키로 좁힌다. 모르는 오류는 `unknown` + 원문. */
export type AuthErrorCode =
  | "account_exists"
  | "invalid_credentials"
  | "confirmation_required"
  | "weak_password"
  | "email_invalid"
  | "rate_limited"
  | "network"
  | "not_configured"
  | "session_expired"
  | "unknown";

export interface ClassifiedAuthError {
  code: AuthErrorCode;
  /** 서버가 준 원문 — unknown 일 때 그대로 보여 줄 마지막 수단. */
  raw: string;
}

/**
 * Supabase GoTrue 오류 문구(영문, 버전마다 조금씩 다름)를 안정된 코드로 매핑한다.
 * 문구 전체 일치가 아니라 핵심 어구로 잡는다 — 서버 업그레이드에 덜 깨진다.
 */
export function classifyAuthError(
  message: string | null | undefined,
  hint?: { status?: number | null; name?: string | null }
): ClassifiedAuthError {
  const raw = (message || "").trim();
  const m = raw.toLowerCase();
  const name = (hint?.name || "").toLowerCase();

  // 재설정 세션이 이미 끊겼다 — updateUser() 를 유효한 세션 없이 부르면
  // supabase-js 가 이 이름/문구로 던진다. 재설정 화면은 이 코드를 "링크가
  // 만료·무효했다" 로 다시 보여 준다.
  if (
    name === "authsessionmissingerror" ||
    m.includes("auth session missing") ||
    m.includes("session missing") ||
    m.includes("session not found") ||
    m.includes("session_not_found") ||
    m.includes("jwt expired")
  ) {
    return { code: "session_expired", raw };
  }

  if (
    name === "typeerror" ||
    name === "authretryablefetcherror" ||
    m.includes("failed to fetch") ||
    m.includes("network") ||
    m.includes("load failed") ||
    m.includes("fetch")
  ) {
    return { code: "network", raw };
  }
  if (m.includes("already registered") || m.includes("already been registered") || m.includes("user already exists")) {
    return { code: "account_exists", raw };
  }
  if (m.includes("invalid login credentials") || m.includes("invalid credentials") || m.includes("invalid email or password")) {
    return { code: "invalid_credentials", raw };
  }
  if (m.includes("email not confirmed") || m.includes("not confirmed")) {
    return { code: "confirmation_required", raw };
  }
  if (m.includes("password") && (m.includes("at least") || m.includes("weak") || m.includes("should contain") || m.includes("too short"))) {
    return { code: "weak_password", raw };
  }
  if (m.includes("unable to validate email") || m.includes("invalid email") || (m.includes("email") && m.includes("invalid"))) {
    return { code: "email_invalid", raw };
  }
  if (m.includes("rate limit") || m.includes("too many requests") || hint?.status === 429) {
    return { code: "rate_limited", raw };
  }
  return { code: "unknown", raw };
}

/** 로컬 표시 이름 — 이름 우선, 없으면 이메일 앞부분. 없으면 빈 문자열. */
export function displayLabelFor(name: string, email: string): string {
  const n = name.trim();
  if (n) return n;
  return (email.split("@")[0] || "").trim();
}
