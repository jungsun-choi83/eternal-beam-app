/**
 * 실제 Supabase 인증 — 로그인 / 회원가입 / 세션 복원 / 토큰 갱신 / 로그아웃.
 *
 * 예전 auth-screen 은 1.5초 기다렸다가 localStorage 에 이메일을 쓰는 것이 전부였다
 * (비밀번호는 아예 쓰이지 않았다). 프리미엄 API 는 검증된 JWT 를 요구하므로
 * 그대로는 아무도 구매할 수 없다.
 *
 * ── 신원에 관한 중요한 점 ────────────────────────────────────────────────────
 * 로그인해도 로컬 user_id 를 Supabase sub 로 **덮어쓰지 않는다.** 기존 데이터
 * (지갑·생성 자산·구매 원장)가 전부 텍스트 user_id 로 키가 잡혀 있어서, 그러면
 * 통째로 고아가 된다. 대신 서버가 sub → Eternal Beam 신원을 확정해 주고
 * (backend/services/identity_service.py), 프론트는 syncEternalBeamIdentity() 로
 * 그 값을 받아 로컬에 반영한다.
 */

import { supabase } from "@/app/config/supabase";
import { setEternalBeamUserId } from "./eternal-beam-user.ts";
import { classifyAuthError, type AuthErrorCode } from "./auth-form.ts";

export type AuthResult =
  | { ok: true; needsEmailConfirmation: boolean }
  | { ok: false; code: AuthErrorCode; message: string };

export function isSupabaseAuthConfigured(): boolean {
  return Boolean(supabase);
}

const NOT_CONFIGURED =
  "인증이 설정되지 않았습니다. VITE_SUPABASE_URL 과 VITE_SUPABASE_ANON_KEY 를 확인하세요.";

function failure(err: { message?: string; status?: number | null; name?: string } | null | undefined): AuthResult {
  const c = classifyAuthError(err?.message, { status: err?.status ?? null, name: err?.name ?? null });
  return { ok: false, code: c.code, message: c.raw || c.code };
}

/** fetch 자체가 던진 경우(오프라인·DNS·CORS) — GoTrue 오류 객체가 아니다. */
function thrown(e: unknown): AuthResult {
  const err = e as { message?: string; name?: string } | null;
  return failure({ message: err?.message, name: err?.name });
}

export async function signInWithPassword(
  email: string,
  password: string
): Promise<AuthResult> {
  if (!supabase) return { ok: false, code: "not_configured", message: NOT_CONFIGURED };
  try {
    const { error } = await supabase.auth.signInWithPassword({
      email: email.trim(),
      password,
    });
    if (error) return failure(error);
  } catch (e) {
    return thrown(e);
  }
  return { ok: true, needsEmailConfirmation: false };
}

/**
 * 확인 메일이 **어디로 돌아올 것인가.**
 *
 * 넘기지 않으면 Supabase 프로젝트의 Site URL 이 쓰인다 — 앱이 통제하지 못하는
 * 값이다. 이 서비스는 origin 이 여럿이고(eternalbeam.com / soultrace… /
 * device…), Site URL 이 그중 다른 곳을 가리키면 확인 링크가 **엉뚱한 origin**
 * 에 떨어진다. 그러면 두 가지가 동시에 깨진다:
 *
 *   * 세션이 그쪽 origin 의 localStorage 에 저장된다 → 앱은 여전히 로그아웃
 *   * Soul Trace 핸드오프도 origin 단위라 그쪽에서는 보이지 않는다 → 편지 증발
 *
 * 그래서 **지금 서 있는 origin 의 경로**를 명시한다. Soul Trace 에서 들어온
 * 가입이면 /soul-trace/import 로 정확히 돌아오고, 그 화면이 저장된 핸드오프로
 * 이어서 진행한다.
 */
function defaultEmailRedirectTo(): string | undefined {
  try {
    const origin = window.location.origin;
    if (!origin) return undefined;
    const path = window.location.pathname.replace(/\/+$/, "") || "/";
    return path === "/soul-trace/import" ? `${origin}/soul-trace/import` : `${origin}/`;
  } catch {
    return undefined;
  }
}

/**
 * 가입 — **이름 · 이메일 · 비밀번호** 세 값이 전부다.
 *
 * 이름은 Supabase Auth 의 user_metadata(`full_name`)에 저장한다. 이 앱에는
 * 별도 프로필 테이블이 없고 서버 신원(identity_service)은 sub/email 만 본다 —
 * user_metadata 가 이름의 **유일한** 권위 있는 저장소다. 두 번째 프로필 원천을
 * 만들지 않는다. (`full_name` 은 Supabase 대시보드·OAuth 공급자가 쓰는 관례 키.)
 *
 * 반려/기기 호출 이름은 여기에 **없다** — 계정과 무관한 펫 온보딩/기기 설정 값이다.
 */
export async function signUpWithPassword(
  email: string,
  password: string,
  options: { emailRedirectTo?: string; name?: string } = {}
): Promise<AuthResult> {
  if (!supabase) return { ok: false, code: "not_configured", message: NOT_CONFIGURED };
  const emailRedirectTo = options.emailRedirectTo?.trim() || defaultEmailRedirectTo();
  const fullName = options.name?.trim() || "";
  let data: { session: unknown } | null = null;
  try {
    const r = await supabase.auth.signUp({
      email: email.trim(),
      password,
      options: {
        ...(emailRedirectTo ? { emailRedirectTo } : {}),
        ...(fullName ? { data: { full_name: fullName } } : {}),
      },
    });
    if (r.error) return failure(r.error);
    data = r.data;
  } catch (e) {
    return thrown(e);
  }
  // 이메일 확인이 켜진 프로젝트에서는 session 이 null 로 온다 — 확인 전까지
  // 토큰이 없으므로 구매도 불가능하다. 호출부가 안내할 수 있게 알려 준다.
  //
  // ⚠️ 현재 이 프로젝트는 mailer_autoconfirm = true (확인 메일 꺼짐)라 가입 즉시
  //    세션이 온다. 그래도 이 분기를 지우지 않는다 — 설정은 대시보드에서 언제든
  //    켜지고, 켜지는 순간 이 값이 유일한 안내 근거가 된다.
  return { ok: true, needsEmailConfirmation: !data?.session };
}

/**
 * 비밀번호 재설정 메일. 링크는 defaultEmailRedirectTo 와 같은 규칙으로 **지금
 * 서 있는 origin** 으로 돌아온다(다른 origin 의 Site URL 로 떨어지면 세션이
 * 그쪽에 갇힌다 — signUp 주석 참고).
 */
export async function sendPasswordReset(
  email: string,
  options: { redirectTo?: string } = {}
): Promise<AuthResult> {
  if (!supabase) return { ok: false, code: "not_configured", message: NOT_CONFIGURED };
  const redirectTo = options.redirectTo?.trim() || defaultEmailRedirectTo();
  try {
    const { error } = await supabase.auth.resetPasswordForEmail(
      email.trim(),
      redirectTo ? { redirectTo } : undefined
    );
    if (error) return failure(error);
  } catch (e) {
    return thrown(e);
  }
  return { ok: true, needsEmailConfirmation: false };
}

/**
 * 비밀번호 재설정 — **지금 세션**의 비밀번호를 바꾼다. 새 계정을 만들지 않고,
 * 신원(user_identity_links)도 건드리지 않는다.
 *
 * 이 세션은 재설정 이메일 링크가 세운 recovery 세션이어야 정상 경로다. 세션이
 * 아예 없으면(링크가 만료됐거나, 다른 브라우저/탭에서 이미 링크를 열었거나
 * 이미 한 번 써서 소비됐다) supabase-js 가 AuthSessionMissingError 를 던지고,
 * classifyAuthError 가 그것을 `session_expired` 로 분류한다 — 화면은 그 코드로
 * "링크가 만료됐다" 를 보여 준다.
 */
export async function updatePassword(newPassword: string): Promise<AuthResult> {
  if (!supabase) return { ok: false, code: "not_configured", message: NOT_CONFIGURED };
  try {
    const { error } = await supabase.auth.updateUser({ password: newPassword });
    if (error) return failure(error);
  } catch (e) {
    return thrown(e);
  }
  return { ok: true, needsEmailConfirmation: false };
}

export interface SessionProfile {
  email: string | null;
  /** user_metadata.full_name — 가입 때 넣은 이름. 없으면 null (지어내지 않는다). */
  name: string | null;
}

function profileOf(user: { email?: string | null; user_metadata?: Record<string, unknown> | null } | null | undefined): SessionProfile | null {
  if (!user) return null;
  const meta = (user.user_metadata || {}) as Record<string, unknown>;
  const rawName = typeof meta.full_name === "string" ? meta.full_name : typeof meta.name === "string" ? meta.name : "";
  const name = rawName.trim();
  return { email: user.email?.trim() || null, name: name || null };
}

/** 현재 세션의 표시용 프로필. 세션이 없으면 null. */
export async function getSessionProfile(): Promise<SessionProfile | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return profileOf(data?.session?.user);
}

export async function signOut(): Promise<void> {
  if (!supabase) return;
  await supabase.auth.signOut();
}

/** 현재 액세스 토큰. 세션이 없으면 null. */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data?.session?.access_token?.trim() || null;
}

/** 401 복구용 강제 갱신. 실패/세션 없음은 null 로 명확히 돌려준다. */
export async function refreshAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data, error } = await supabase.auth.refreshSession();
  if (error) return null;
  return data.session?.access_token?.trim() || null;
}

export async function hasSession(): Promise<boolean> {
  return (await getAccessToken()) != null;
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/**
 * 서버가 확정한 Eternal Beam 신원을 받아 로컬에 반영한다.
 *
 * 이것이 "고아 만들지 않기"의 마지막 조각이다. 검증된 이메일로 로그인하면 서버는
 * 소문자 이메일을 신원으로 확정하는데, 그 값이 바로 예전 로그인 화면이 쓰던 값이다
 * → 기존 지갑·자산·구매가 그대로 붙는다. 로컬도 같은 값을 써야 잔액 조회 같은
 * 레거시 경로가 갈라지지 않는다.
 */
export async function syncEternalBeamIdentity(): Promise<string | null> {
  const token = await getAccessToken();
  if (!token) return null;
  try {
    const res = await fetch(`${apiBase()}/api/v1/pet/premium/identity`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { user_id?: string };
    const id = (body.user_id || "").trim();
    if (!id) return null;
    setEternalBeamUserId(id);
    return id;
  } catch {
    return null;
  }
}

/**
 * 세션 변화 구독 — 복원 / 갱신 / 로그아웃.
 *
 * supabase-js 가 액세스 토큰 갱신을 자동으로 처리하고 TOKEN_REFRESHED 를 쏜다.
 * 우리는 신원만 다시 맞춰 주면 된다.
 */
/**
 * 세션 변화 구독 — 복원 / 갱신 / 로그아웃 / **비밀번호 재설정 링크**.
 *
 * supabase-js 가 액세스 토큰 갱신을 자동으로 처리하고 TOKEN_REFRESHED 를 쏜다.
 * 우리는 신원만 다시 맞춰 주면 된다.
 *
 * 재설정 링크를 열면 GoTrue 는 유효한 세션을 세우면서도(signedIn = true)
 * PASSWORD_RECOVERY 이벤트를 쏜다 — 그냥 로그인한 것과 겉모습이 같다. 세 번째
 * 인자로 그 구분을 그대로 넘긴다: 호출부는 이 순간을 "로그인 완료"로 다루지
 * 않아야 한다(펫 등록·홈 이동 같은 정상 로그인 부수 효과를 건너뛴다 —
 * EternalBeamApp 의 세션 구독 참고). 재설정 화면 자신은 바로 이 플래그로
 * "링크가 방금 세션을 세웠다"를 알아챈다.
 */
export function onAuthStateChange(
  cb: (signedIn: boolean, profile: SessionProfile | null, isPasswordRecovery: boolean) => void
): () => void {
  if (!supabase) return () => {};
  const { data } = supabase.auth.onAuthStateChange((event, session) => {
    const signedIn = Boolean(session?.access_token);
    const isPasswordRecovery = event === "PASSWORD_RECOVERY";
    if (signedIn && (event === "SIGNED_IN" || event === "INITIAL_SESSION")) {
      void syncEternalBeamIdentity();
    }
    cb(signedIn, signedIn ? profileOf(session?.user) : null, isPasswordRecovery);
  });
  return () => data.subscription.unsubscribe();
}
