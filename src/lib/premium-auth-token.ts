/**
 * 프리미엄 API 용 인증 토큰.
 *
 * ⚠️ **현재 이 앱에는 실제 로그인이 없다.** auth-screen.tsx 는
 * setEternalBeamUserId(email) 로 localStorage 에 문자열을 쓸 뿐이고, Supabase
 * 세션도 JWT 도 만들지 않는다. 반면 프로덕션 프리미엄 라우터는 검증된 Bearer
 * 토큰을 요구한다(backend/auth.py).
 *
 * 그래서 이 모듈은 **토큰을 지어내지 않는다.** 실제 세션이 있으면 그 액세스
 * 토큰을 주고, 없으면 null 을 준다 — 호출부는 그때 "로그인이 필요합니다" 상태를
 * 보여야 하고, 구매를 시도해서는 안 된다.
 *
 * 개발 편의로 ALLOW_INSECURE_TEST_AUTH 우회가 하나 있다. 백엔드에도 같은 이름의
 * 게이트가 있어야만 동작하고(기본 꺼짐), 프로덕션 빌드에서는 아예 켜지지 않는다.
 */

const DEV_BYPASS_FLAG = "VITE_ALLOW_INSECURE_TEST_AUTH";

function isDev(): boolean {
  try {
    return Boolean((import.meta as { env?: Record<string, unknown> }).env?.DEV);
  } catch {
    return false;
  }
}

function devBypassEnabled(): boolean {
  if (!isDev()) return false; // 프로덕션 번들에서는 절대 켜지지 않는다
  try {
    const v = (import.meta as { env?: Record<string, string> }).env?.[DEV_BYPASS_FLAG];
    return String(v ?? "").trim() === "1";
  } catch {
    return false;
  }
}

export type AuthTokenResult =
  | { token: string; source: "supabase" | "dev-bypass" }
  | { token: null; source: "none"; reason: "no-session" | "no-client" };

/**
 * 현재 액세스 토큰. 없으면 null — **절대 대체 토큰을 만들지 않는다.**
 */
export async function getPremiumAccessToken(): Promise<AuthTokenResult> {
  try {
    const { getAccessToken, isSupabaseAuthConfigured } = await import("./supabase-auth.ts");
    if (isSupabaseAuthConfigured()) {
      // supabase-js 가 만료 전 갱신을 자동으로 처리하므로, 매 호출에서 세션을
      // 다시 읽는 것만으로 항상 유효한 토큰을 얻는다.
      const token = await getAccessToken();
      if (token) return { token, source: "supabase" };
    } else if (!devBypassEnabled()) {
      return { token: null, source: "none", reason: "no-client" };
    }
  } catch {
    /* 클라이언트가 없거나 초기화 실패 — 아래 우회 판정으로 간다 */
  }

  if (devBypassEnabled()) {
    // 백엔드의 ALLOW_INSECURE_TEST_AUTH 가 켜져 있어야만 통과한다.
    const { getEternalBeamUserId } = await import("./eternal-beam-user.ts");
    return { token: `test:${getEternalBeamUserId()}`, source: "dev-bypass" };
  }

  return { token: null, source: "none", reason: "no-session" };
}
