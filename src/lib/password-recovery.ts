/**
 * 비밀번호 재설정 링크 진입 감지.
 *
 * sendPasswordReset() 이 보낸 메일의 링크는 이 origin 으로 돌아온다
 * (supabase-auth.ts defaultEmailRedirectTo). supabase-js 는 기본 설정
 * (detectSessionInUrl: true, flowType: 'implicit') 그대로다 — 즉 성공한
 * 링크는 URL **해시**에 `access_token`·`type=recovery` 를 싣고 오고,
 * 만료·이미 쓴 링크는 `error`/`error_code` 를 싣고 온다. supabase-js 클라이언트가
 * 그 해시를 비동기로 읽어 세션을 세우거나(성공) 실패를 기록한 뒤 **해시를
 * 지운다.**
 *
 * 이 프로젝트에는 해시 기반으로 이 origin 에 돌아오는 이메일 링크가 재설정
 * 하나뿐이다(가입 확인은 mailer_autoconfirm 로 꺼져 있다 — supabase-auth.ts
 * signUpWithPassword 주석 참고). 그래서 `type` 없이 `error` 만 실려 있는
 * 해시도 안전하게 "재설정 링크가 만료·무효했다" 로 읽는다.
 *
 * resolveInitialScreen() 같은 동기 진입 판정이 이 값을 쓴다 — 화면이 조립되기
 * **전**에 읽어야 supabase-js 가 지우기 전의 해시를 볼 수 있다(자세한 타이밍은
 * EternalBeamApp.tsx 의 호출부 주석).
 */

export type PasswordRecoveryEntry =
  | { kind: "recovery" }
  | { kind: "recovery_error"; description: string };

/** 탭을 새로고침해도(해시는 이미 지워진 뒤) 재설정 화면으로 계속 보내는 표식. */
const RECOVERY_MARKER_KEY = "eternal_beam_password_recovery_v1";

function parseHash(): PasswordRecoveryEntry | null {
  const raw = (window.location.hash || "").replace(/^#/, "");
  if (!raw) return null;
  const params = new URLSearchParams(raw);
  if (params.get("type") === "recovery" && params.get("access_token")) {
    return { kind: "recovery" };
  }
  const error = params.get("error");
  const errorCode = params.get("error_code");
  if ((error || errorCode) && !params.get("type")) {
    return {
      kind: "recovery_error",
      description: params.get("error_description") || errorCode || error || "",
    };
  }
  return null;
}

/**
 * 지금이 비밀번호 재설정 진입인지 — URL 해시를 먼저 보고, 없으면(이미 지워진
 * 새로고침) sessionStorage 표식을 본다.
 *
 * 해시에서 `{ kind: "recovery" }` 를 읽으면 그 순간 표식을 남긴다 — 실제로
 * 세션이 섰는지는 여기서 확정하지 않는다(비동기라 아직 모른다), 화면이
 * hasSession()/onAuthStateChange 로 다시 확인한다.
 */
export function passwordRecoveryEntry(): PasswordRecoveryEntry | null {
  if (typeof window === "undefined") return null;

  let fromHash: PasswordRecoveryEntry | null = null;
  try {
    fromHash = parseHash();
  } catch {
    fromHash = null;
  }
  if (fromHash?.kind === "recovery") {
    try {
      sessionStorage.setItem(RECOVERY_MARKER_KEY, "1");
    } catch {
      /* 용량 초과·프라이빗 모드 — 새로고침 재개만 못 할 뿐, 지금 진입은 그대로 된다 */
    }
    return fromHash;
  }
  if (fromHash?.kind === "recovery_error") return fromHash;

  try {
    if (sessionStorage.getItem(RECOVERY_MARKER_KEY) === "1") return { kind: "recovery" };
  } catch {
    /* ignore */
  }
  return null;
}

/**
 * 재설정을 마쳤거나(성공) 사용자가 그만뒀을 때(로그인으로 돌아가기) — 표식과
 * URL 해시를 함께 지운다. 지우지 않으면 다음 새로고침이 또 재설정 화면으로
 * 떨어진다.
 */
export function clearPasswordRecoveryEntry(): void {
  try {
    sessionStorage.removeItem(RECOVERY_MARKER_KEY);
  } catch {
    /* ignore */
  }
  try {
    if (window.location.hash) {
      const url = new URL(window.location.href);
      url.hash = "";
      window.history.replaceState(window.history.state, "", url.toString());
    }
  } catch {
    /* ignore (테스트/SSR 환경) */
  }
}
