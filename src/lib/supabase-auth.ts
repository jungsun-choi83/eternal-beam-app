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

export type AuthResult =
  | { ok: true; needsEmailConfirmation: boolean }
  | { ok: false; message: string };

export function isSupabaseAuthConfigured(): boolean {
  return Boolean(supabase);
}

const NOT_CONFIGURED =
  "인증이 설정되지 않았습니다. VITE_SUPABASE_URL 과 VITE_SUPABASE_ANON_KEY 를 확인하세요.";

export async function signInWithPassword(
  email: string,
  password: string
): Promise<AuthResult> {
  if (!supabase) return { ok: false, message: NOT_CONFIGURED };
  const { error } = await supabase.auth.signInWithPassword({
    email: email.trim(),
    password,
  });
  if (error) return { ok: false, message: error.message };
  return { ok: true, needsEmailConfirmation: false };
}

export async function signUpWithPassword(
  email: string,
  password: string
): Promise<AuthResult> {
  if (!supabase) return { ok: false, message: NOT_CONFIGURED };
  const { data, error } = await supabase.auth.signUp({
    email: email.trim(),
    password,
  });
  if (error) return { ok: false, message: error.message };
  // 이메일 확인이 켜진 프로젝트에서는 session 이 null 로 온다 — 확인 전까지
  // 토큰이 없으므로 구매도 불가능하다. 호출부가 안내할 수 있게 알려 준다.
  return { ok: true, needsEmailConfirmation: !data.session };
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
export function onAuthStateChange(cb: (signedIn: boolean) => void): () => void {
  if (!supabase) return () => {};
  const { data } = supabase.auth.onAuthStateChange((event, session) => {
    const signedIn = Boolean(session?.access_token);
    if (signedIn && (event === "SIGNED_IN" || event === "INITIAL_SESSION")) {
      void syncEternalBeamIdentity();
    }
    cb(signedIn);
  });
  return () => data.subscription.unsubscribe();
}
