/**
 * 비밀번호 재설정 — 구조 고정.
 *
 *   Sign In → Forgot password → 이메일 입력 → 재설정 메일 발송
 *   → 링크 열기 → Reset Password 화면 → 새 비밀번호 + 확인
 *   → Supabase 비밀번호 갱신 → 성공 → (로그아웃) → 로그인
 *
 * 순수 규칙(새 비밀번호 검증·오류 분류)은 auth-form.test.ts,
 * 진입 감지(해시/표식)는 password-recovery.test.ts 가 실행한다. 여기서는
 * 화면·셸·어댑터의 배선이 그 계약을 지키는지 소스로 확인한다.
 */
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { memorialT } from "../components/memorial/memorial-i18n.ts";

const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const auth = readFileSync("src/components/memorial/auth-screen.tsx", "utf8");
const reset = readFileSync("src/components/memorial/reset-password-screen.tsx", "utf8");
const supa = readFileSync("src/lib/supabase-auth.ts", "utf8");
const recovery = readFileSync("src/lib/password-recovery.ts", "utf8");
const authForm = readFileSync("src/lib/auth-form.ts", "utf8");
const i18nSrc = readFileSync("src/components/memorial/memorial-i18n.ts", "utf8");

function slice(src: string, from: string, len = 1800): string {
  const i = src.indexOf(from);
  assert.ok(i >= 0, `'${from}' 이 없다`);
  return src.slice(i, i + len);
}

describe("1. Sign In → Forgot password → 이메일 입력 → 재설정 메일 발송", () => {
  it("Sign In 화면에 Forgot password 가 있고 sendPasswordReset 을 부른다", () => {
    assert.match(auth, /onClick=\{\(\) => void handleForgotPassword\(\)\}/);
    const fn = slice(auth, "const handleForgotPassword = async", 900);
    assert.match(fn, /sendPasswordReset\(normalizedEmail\)/);
  });

  it("이메일 없이는 보내지 않는다 — 서버 호출 전에 막는다", () => {
    const fn = slice(auth, "const handleForgotPassword = async", 900);
    const guard = fn.indexOf("if (errors.email) {");
    const call = fn.indexOf("sendPasswordReset(");
    assert.ok(guard > 0 && call > guard, "이메일 검증이 발송 호출보다 앞에 있어야 한다");
  });

  it("발송 성공은 화면에 안내를 띄운다", () => {
    assert.match(auth, /setNotice\(\{ kind: "reset-sent", email: normalizedEmail \}\)/);
    assert.match(auth, /notice\?\.kind === "reset-sent"/);
  });

  it("어댑터는 진짜 Supabase resetPasswordForEmail 을 부르고, 지금 origin 으로 링크를 되돌린다", () => {
    const fn = slice(supa, "export async function sendPasswordReset", 700);
    assert.match(fn, /supabase\.auth\.resetPasswordForEmail\(/);
    assert.match(fn, /redirectTo \? \{ redirectTo \} : undefined/);
  });
});

describe("2. 재설정 링크 → Reset Password 화면 (Home 이 아니다)", () => {
  it("resolveInitialScreen 은 다른 durable 마커보다 먼저 재설정 링크를 본다", () => {
    const fn = slice(app, "function resolveInitialScreen", 900);
    const recoveryIdx = fn.indexOf("passwordRecoveryEntry()");
    const billingIdx = fn.indexOf("billingReturnEntry()");
    assert.ok(recoveryIdx > 0 && billingIdx > 0 && recoveryIdx < billingIdx);
    assert.match(fn, /if \(passwordRecoveryEntry\(\)\) return 'resetPassword'/);
  });

  it("진입 감지는 URL 해시(type=recovery)를 근거로 한다 — durable 저장소 흉내가 아니다", () => {
    assert.match(recovery, /type"\) === "recovery"/);
    assert.match(recovery, /access_token/);
  });

  it("resetPassword 화면은 앱 전체 화면(unframed)이고 데스크톱 내비게이션이 없다 — Sign Up/In 과 같은 대우", () => {
    assert.match(slice(app, "const AUTH_ENTRY_SCREENS", 200), /'resetPassword'/);
    assert.match(slice(app, "const DESKTOP_NAV_HIDDEN_SCREENS", 250), /'resetPassword',/);
  });

  it("resetPassword 로 이어지는 앱 내 버튼이 없다 — 오직 재설정 링크로만 열린다", () => {
    assert.doesNotMatch(app, /navigateTo\('resetPassword'\)/);
    assert.doesNotMatch(app, /replaceScreen\('resetPassword'\)/);
  });

  it("화면은 hasSession/onAuthStateChange 로 실제 세션이 섰는지 다시 확인한다", () => {
    assert.match(reset, /onAuthStateChange\(\(signedIn\) => \{/);
    assert.match(reset, /void hasSession\(\)\.then/);
  });
});

describe("3. Reset Password 화면 — 새 비밀번호 + 확인", () => {
  it("정확히 두 입력만 있다", () => {
    const ids = [...reset.matchAll(/id="reset-([a-z-]+)"/g)].map((m) => m[1]);
    assert.deepEqual(ids, ["new-password", "confirm-password"]);
  });

  it("Update Password / Back to Sign In 두 동작이 ready 상태에 있다", () => {
    const readyBlock = slice(reset, 'status === "ready" ? (', 9000);
    assert.match(readyBlock, /\{a\.updatePassword\}/);
    assert.match(readyBlock, /\{a\.backToSignIn\}/);
    assert.match(readyBlock, /type="submit"/);
  });

  it("이 화면은 이메일도, 반려/기기 이름도 요구하지 않는다", () => {
    assert.doesNotMatch(reset, /petName|PetName|type="email"/);
  });
});

describe("4. Supabase — 기존 세션·클라이언트를 그대로 쓰고, 새 계정을 만들지 않는다", () => {
  it("updatePassword 는 supabase.auth.updateUser 를 부른다 (signUp 이 아니다)", () => {
    const fn = slice(supa, "export async function updatePassword", 700);
    assert.match(fn, /supabase\.auth\.updateUser\(\{ password: newPassword \}\)/);
    assert.doesNotMatch(fn, /signUp\(/);
  });

  it("어댑터를 새로 만들지 않았다 — 같은 app/config/supabase 클라이언트를 쓴다", () => {
    assert.equal((supa.match(/from "@\/app\/config\/supabase"/g) || []).length, 1);
  });

  it("화면은 어댑터의 updatePassword 를 호출한다", () => {
    assert.match(reset, /const r = await updatePassword\(newPassword\)/);
  });

  it("백엔드 신원 아키텍처(identity_service)를 건드리지 않는다 — updatePassword 는 그 값들을 참조하지 않는다", () => {
    const fn = slice(supa, "export async function updatePassword", 700);
    assert.doesNotMatch(fn, /identity_service|user_identity_links|syncEternalBeamIdentity|setEternalBeamUserId/);
  });
});

describe("5. 검증 — 필수 + 일치 확인", () => {
  it("제출은 서버 호출 전에 검증부터 한다", () => {
    const fn = slice(reset, "const handleSubmit = async", 1200);
    const guard = fn.indexOf("if (hasFieldErrors(errors)) {");
    const call = fn.indexOf("updatePassword(");
    assert.ok(guard > 0 && call > guard, "검증 가드가 서버 호출보다 앞에 있어야 한다");
    assert.match(fn.slice(guard, guard + 120), /return;/);
  });

  it("검증은 auth-form 의 순수 규칙(validateNewPassword)을 쓴다", () => {
    assert.match(reset, /validateNewPassword\(\{ password: newPassword, confirm: confirmPassword \}\)/);
  });

  it("불일치는 확인란 오류로 막힌다 — auth-form 이 그 판정을 갖는다", () => {
    assert.match(authForm, /input\.confirm !== input\.password/);
    assert.match(authForm, /"password_mismatch"/);
  });

  it("Supabase 최소 길이 규칙을 그대로 따른다 — 더 강한 규칙을 만들지 않는다", () => {
    assert.match(authForm, /input\.password\.length < MIN_PASSWORD_LENGTH/);
  });

  it("폼은 noValidate 로 우리 검증을 쓴다", () => {
    assert.match(reset, /onSubmit=\{handleSubmit\} noValidate>/);
  });
});

describe("6. 로딩 / 오류 / 성공 상태", () => {
  it("네 가지 상태를 모두 갖는다: verifying · ready · success · expired", () => {
    for (const s of ["verifying", "ready", "success", "expired"]) {
      assert.match(reset, new RegExp(`"${s}"`));
    }
  });

  it("제출 중에는 로딩 스피너를 보여주고 버튼을 잠근다", () => {
    const readyBlock = slice(reset, 'status === "ready" ? (', 9000);
    assert.match(readyBlock, /disabled=\{isSubmitting\}/);
    assert.match(readyBlock, /aria-busy=\{isSubmitting \|\| undefined\}/);
    assert.match(readyBlock, /eb-btn__spinner/);
  });

  it("서버 오류는 화면에 그대로 보여준다(role=alert)", () => {
    assert.match(reset, /serverError \? \(\s*<p className="auth-field-error eb-entry__server-error" role="alert">/);
  });

  it("만료/무효 세션은 session_expired 코드로 별도 상태로 전환한다", () => {
    const fn = slice(reset, "const handleSubmit = async", 1200);
    assert.match(fn, /if \(r\.code === "session_expired"\) \{\s*setStatus\("expired"\);/);
  });
});

describe("7. 재설정 URL/해시 처리", () => {
  it("성공 링크(type=recovery)와 오류 링크(error/error_code)를 구분해서 읽는다", () => {
    assert.match(recovery, /params\.get\("type"\) === "recovery" && params\.get\("access_token"\)/);
    assert.match(recovery, /error_code/);
  });

  it("정상 로그인 세션은 이 진입 판정을 타지 않는다 — 해시/표식이 없으면 null", () => {
    assert.match(recovery, /return null;\s*\}\s*$/m);
  });

  it("성공한 뒤에는 표식/해시를 안전하게 지운다", () => {
    assert.match(reset, /clearPasswordRecoveryEntry\(\);/);
    assert.match(recovery, /export function clearPasswordRecoveryEntry/);
    assert.match(recovery, /sessionStorage\.removeItem\(RECOVERY_MARKER_KEY\)/);
  });
});

describe("8. 성공한 재설정은 새 비밀번호로 로그인하게 만든다", () => {
  it("성공하면 재설정 세션을 로그아웃한다 — 다음 로그인이 새 비밀번호를 검증한다", () => {
    const fn = slice(reset, "const handleSubmit = async", 1400);
    assert.match(fn, /await signOut\(\);/);
    assert.match(fn, /setStatus\("success"\);/);
  });

  it("성공 화면의 Continue to Sign In 은 로그인 화면으로 보낸다(onDone)", () => {
    const successBlock = slice(reset, 'status === "success" ? (', 1200);
    assert.match(successBlock, /onClick=\{\(\) => void handleBackToSignIn\(\)\}/);
    assert.match(successBlock, /\{a\.continueToSignIn\}/);
    assert.match(reset, /const handleBackToSignIn = async \(\) => \{[\s\S]{0,200}onDone\(\);/);
  });

  it("앱은 onDone 을 로그인 화면으로 잇는다", () => {
    assert.match(app, /<ResetPasswordScreen language=\{language\} onDone=\{\(\) => replaceScreen\('login'\)\} \/>/);
  });
});

describe("9. 정상 세션은 여전히 Home 으로 간다", () => {
  it("재설정 진입 판정은 정상 새로고침 세션-복원 로직과 별개다", () => {
    // 그 세션-복원 효과는 getStarted 화면일 때만 hasSession() 을 확인해 home 으로
    // 보낸다 — resetPassword 진입은 그 이전에(resolveInitialScreen 에서) 이미
    // 갈라져 있으므로 서로 간섭하지 않는다.
    const fx = slice(app, "if (screen !== 'getStarted') return", 400);
    assert.match(fx, /navigateTo\('home'\)/);
  });

  it("일반 로그인/세션 복원 이벤트는 password_recovery 로 오분류되지 않는다", () => {
    assert.match(supa, /const isPasswordRecovery = event === "PASSWORD_RECOVERY";/);
    assert.match(supa, /event === "SIGNED_IN" \|\| event === "INITIAL_SESSION"/);
  });

  it("재설정 이벤트는 정상 로그인 부수 효과(펫 등록)를 건너뛴다", () => {
    assert.match(app, /if \(!signedIn \|\| isPasswordRecovery\) return/);
  });
});

describe("10. 기존 인증 계약은 그대로 보존된다", () => {
  it("Sign Up 은 여전히 이름 · 이메일 · 비밀번호다", () => {
    const ids = [...auth.matchAll(/id="auth-([a-z]+)"/g)].map((m) => m[1]);
    assert.deepEqual(ids, ["name", "email", "password"]);
  });

  it("확인 메일 흐름은 그대로다", () => {
    assert.match(supa, /needsEmailConfirmation: !data\?\.session/);
  });

  it("세션 복원 / 로그아웃은 그대로다", () => {
    assert.match(supa, /export async function hasSession/);
    assert.match(supa, /export async function signOut/);
  });
});

describe("11. 한국어 / 영어", () => {
  const REQUIRED = [
    "resetPasswordHeading", "resetPasswordLead", "newPassword", "confirmPassword",
    "updatePassword", "backToSignIn", "resetVerifying", "resetSuccessTitle",
    "resetSuccessBody", "resetExpiredTitle", "resetExpiredBody", "continueToSignIn",
  ] as const;
  const ERROR_CODES = ["confirm_required", "password_mismatch", "session_expired"] as const;

  for (const lang of ["ko", "en"] as const) {
    it(`${lang}: 모든 재설정 문구가 있고 비어 있지 않다`, () => {
      const a = memorialT(lang).auth as Record<string, unknown>;
      for (const key of REQUIRED) {
        assert.equal(typeof a[key], "string", `${lang}.auth.${key}`);
        assert.ok((a[key] as string).trim().length > 0, `${lang}.auth.${key} 가 비었다`);
      }
      const errors = a.errors as Record<string, string>;
      for (const code of ERROR_CODES) {
        assert.ok(errors[code]?.trim().length > 0, `${lang}.auth.errors.${code}`);
      }
    });
  }

  it("i18n 소스에 재설정 관련 키가 실제로 존재한다", () => {
    assert.match(i18nSrc, /resetPasswordHeading:/);
    assert.match(i18nSrc, /session_expired:/);
  });
});
