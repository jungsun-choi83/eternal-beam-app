/**
 * 첫 방문 + 단순화된 인증 계약 — 구조 고정.
 *
 *   첫 방문(세션 없음) → Get Started → [시작하기 → 회원가입] / [이미 계정 → 로그인]
 *   회원가입 = 이름 · 이메일 · 비밀번호          로그인 = 이메일 · 비밀번호
 *   새로고침(세션 있음) → 홈
 *
 * 예전 가입 폼이 요구하던 반려/기기 호출 이름은 계정 만들기에서 **완전히** 빠졌다.
 * 순수 규칙(검증·오류 분류)은 auth-form.test.ts 가 실행하고, 여기서는 화면·셸의
 * 배선이 그 계약을 지키는지 소스로 확인한다.
 */
import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { memorialT } from "../components/memorial/memorial-i18n.ts";

const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const auth = readFileSync("src/components/memorial/auth-screen.tsx", "utf8");
const start = readFileSync("src/components/memorial/get-started-screen.tsx", "utf8");
const supa = readFileSync("src/lib/supabase-auth.ts", "utf8");
const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");
const petProfile = readFileSync("src/lib/pet-profile.ts", "utf8");
const i18nSrc = readFileSync("src/components/memorial/memorial-i18n.ts", "utf8");

function slice(src: string, from: string, len = 1500): string {
  const i = src.indexOf(from);
  assert.ok(i >= 0, `'${from}' 이 없다`);
  return src.slice(i, i + len);
}

describe("1. 첫 방문 → Get Started", () => {
  it("resolveInitialScreen 의 폴백은 getStarted 다 (qrConnection 스플래시는 없다)", () => {
    const fn = slice(app, "function resolveInitialScreen", 2500);
    assert.match(fn, /return 'getStarted'\n\}/);
    assert.doesNotMatch(app, /qrConnection/);
  });

  it("Get Started 는 자동 타이머로 넘어가지 않는다", () => {
    assert.doesNotMatch(start, /setTimeout|setInterval|SPLASH_/);
    assert.doesNotMatch(app, /SPLASH_FADE_MS|SPLASH_HOLD_MS/);
  });

  it("Get Started 는 앱 셸 안에서 그려지고 데스크톱 내비게이션은 숨는다", () => {
    assert.match(app, /screen === 'getStarted' && \(/);
    assert.match(slice(app, "const DESKTOP_NAV_HIDDEN_SCREENS", 200), /'getStarted',\n  'signup',\n  'login',/);
  });
});

describe("2. Get Started → 회원가입 / 3. 기존 계정 → 로그인", () => {
  it("주 CTA 는 onGetStarted, 보조는 onSignIn 을 부른다", () => {
    assert.match(start, /onClick=\{onGetStarted\}/);
    assert.match(start, /onClick=\{onSignIn\}/);
    assert.match(start, /data-action="get-started"/);
    assert.match(start, /data-action="sign-in"/);
  });

  it("앱은 그 둘을 signup / login 화면으로 잇는다", () => {
    assert.match(app, /onGetStarted=\{handleGetStarted\}/);
    assert.match(app, /onSignIn=\{handleGoToSignIn\}/);
    assert.match(slice(app, "const handleGetStarted", 500), /navigateTo\('signup'\)/);
    assert.match(slice(app, "const handleGoToSignIn", 200), /navigateTo\('login'\)/);
  });

  it("보조 진입은 첫 화면에 실제로 있다 — 기존 사용자가 가입 폼에 떨어지지 않는다", () => {
    for (const lang of ["ko", "en"] as const) {
      assert.ok(memorialT(lang).auth.haveAccount.trim().length > 0);
      assert.ok(memorialT(lang).auth.getStarted.trim().length > 0);
    }
  });
});

describe("4/5. 회원가입은 이름 · 이메일 · 비밀번호만 — 반려/기기 이름 요구 없음", () => {
  it("AuthScreen 은 세 입력만 갖는다 (이름은 가입 모드에서만)", () => {
    const ids = [...auth.matchAll(/id="auth-([a-z]+)"/g)].map((m) => m[1]);
    assert.deepEqual(ids, ["name", "email", "password"]);
    assert.match(auth, /\{isSignup \? \(\s*<div>\s*<label className="sr-only" htmlFor="auth-name">/);
  });

  it("검증은 auth-form 의 순수 규칙을 쓴다", () => {
    assert.match(auth, /validateSignup\(\{ name, email, password \}\)/);
    assert.match(auth, /validateLogin\(\{ email, password \}\)/);
  });

  it("반려/기기 이름은 인증 화면·어댑터·앱 인증 배선 어디에도 없다", () => {
    for (const [label, src] of [
      ["auth-screen", auth],
      ["get-started", start],
      ["supabase-auth", supa],
    ] as const) {
      assert.doesNotMatch(src, /petName|PetName|pet-profile|wakeName|syncPetProfileToDevice/, `${label} 에 반려 이름 의존이 남아 있다`);
    }
    // 앱: AuthScreen 호출부(가입·로그인 블록)에 반려 이름을 넘기지 않는다.
    for (const key of ["signup", "login"] as const) {
      assert.doesNotMatch(slice(app, `screen === '${key}' && (`, 900), /petName|getPetName/);
    }
  });

  it("i18n 의 auth 블록에 반려 이름 키가 없다 (ko/en)", () => {
    for (const lang of ["ko", "en"] as const) {
      const keys = Object.keys(memorialT(lang).auth);
      assert.ok(!keys.some((k) => /pet/i.test(k)), `${lang}.auth 에 ${keys.filter((k) => /pet/i.test(k))}`);
    }
    assert.doesNotMatch(i18nSrc, /petNameRequired|petNamePlaceholder|petNameHint/);
  });

  it("어댑터의 signUp 은 이름·이메일·비밀번호만 받고 이름은 user_metadata.full_name 에 넣는다", () => {
    assert.match(supa, /options: \{ emailRedirectTo\?: string; name\?: string \} = \{\}/);
    assert.match(supa, /data: \{ full_name: fullName \}/);
    assert.doesNotMatch(supa, /Pet 1|pet_name/);
  });
});

describe("6. 가입은 반려/기기 이름 없이 성공한다", () => {
  it("제출 경로는 검증 → signUpWithPassword(email, password, { emailRedirectTo, name }) → onAuthComplete", () => {
    const submit = slice(auth, "const handleSubmit = async", 2600);
    assert.match(submit, /signUpWithPassword\(normalizedEmail, password, \{ emailRedirectTo, name: trimmedName \}\)/);
    assert.match(submit, /onAuthComplete\(label \|\| undefined, mode\)/);
    assert.doesNotMatch(submit, /petName/);
  });

  it("가입 완료는 사진 업로드로, 로그인 완료는 홈으로 간다 (기존 동선 유지)", () => {
    assert.match(slice(app, "screen === 'signup' && (", 1200), /completedMode === 'login' \? 'home' : 'photoUpload'/);
    assert.match(slice(app, "screen === 'login' && (", 1200), /completedMode === 'signup' \? 'photoUpload' : 'home'/);
  });
});

describe("7. 기존 사용자는 그대로 로그인한다", () => {
  it("로그인은 여전히 supabase.auth.signInWithPassword(email, password) 다 — 이름을 묻지 않는다", () => {
    const fn = slice(supa, "export async function signInWithPassword", 700);
    assert.match(fn, /supabase\.auth\.signInWithPassword\(\{\s*email: email\.trim\(\),\s*password,\s*\}\)/);
    assert.doesNotMatch(fn, /name|full_name/);
  });

  it("서버 신원 연결(syncEternalBeamIdentity)은 그대로다 — 기존 지갑·자산이 붙는다", () => {
    assert.match(supa, /export async function syncEternalBeamIdentity/);
    assert.match(auth, /await syncEternalBeamIdentity\(\)/);
  });

  it("로그아웃은 Get Started 로 돌아가며 진짜 세션을 끝낸다", () => {
    const fn = slice(app, "const handleLogout = () => {", 700);
    assert.match(fn, /replaceScreen\('getStarted'\)/);
    assert.match(fn, /m\.signOut\(\)/);
    assert.match(fn, /setUserName\(null\)/);
  });
});

describe("8. 확인 메일 흐름", () => {
  it("어댑터는 세션 없는 가입을 needsEmailConfirmation 으로 알리고 착지 URL 을 넘긴다", () => {
    assert.match(supa, /needsEmailConfirmation: !data\?\.session/);
    assert.match(supa, /emailRedirectTo \? \{ emailRedirectTo \} : \{\}/);
  });

  it("화면은 안내를 띄우고 앱에 들이지 않는다 (onAuthComplete 호출 없음)", () => {
    const branch = slice(auth, "if (r.needsEmailConfirmation) {", 500);
    assert.match(branch, /setNotice\(\{ kind: "confirm-sent", email: normalizedEmail \}\)/);
    assert.match(branch, /return;/);
    assert.doesNotMatch(branch, /onAuthComplete/);
    assert.match(auth, /notice\?\.kind === "confirm-sent"/);
  });

  it("비밀번호 재설정 메일도 어댑터를 거친다", () => {
    assert.match(supa, /supabase\.auth\.resetPasswordForEmail\(/);
    assert.match(auth, /sendPasswordReset\(normalizedEmail\)/);
  });
});

describe("9. 빈/잘못된 자격 증명은 들어가지 못한다", () => {
  it("검증 실패면 서버 호출 전에 return 한다", () => {
    const submit = slice(auth, "const handleSubmit = async", 2600);
    const guard = submit.indexOf("if (hasFieldErrors(errors)) {");
    const call = submit.indexOf("signInWithPassword(");
    assert.ok(guard > 0 && call > guard, "검증 가드가 서버 호출보다 앞에 있어야 한다");
    assert.match(submit.slice(guard, guard + 120), /return;/);
  });

  it("Supabase 미설정 개발 환경에서도 빈 자격 증명은 같은 가드에 걸린다", () => {
    // 예전에는 `email.trim() && password` 일 때만 인증하고 아니면 그냥 통과시켰다.
    assert.doesNotMatch(auth, /isSupabaseAuthConfigured\(\) && email\.trim\(\) && password/);
  });

  it("폼은 브라우저 기본 검증 대신 우리 규칙을 쓴다 (noValidate + submit)", () => {
    assert.match(auth, /<form className="eb-entry__form" onSubmit=\{handleSubmit\} noValidate>/);
    assert.match(auth, /type="submit"/);
  });
});

describe("10. 새로고침 — 세션이 있으면 홈", () => {
  it("폴백 화면(getStarted)일 때만 세션을 확인해 홈으로 보낸다", () => {
    const fx = slice(app, "if (screen !== 'getStarted') return", 400);
    assert.match(fx, /await m\.hasSession\(\)/);
    assert.match(fx, /navigateTo\('home'\)/);
  });

  it("표시 이름은 세션 프로필(user_metadata.full_name)에서 되살린다 — 지어내지 않는다", () => {
    assert.match(app, /onAuthStateChange\(\(signedIn, profile, isPasswordRecovery\) => \{/);
    assert.match(app, /if \(profile\?\.name\) setUserName\(profile\.name\)/);
    assert.match(supa, /typeof meta\.full_name === "string"/);
  });
});

describe("11. 한국어 / 영어 폼", () => {
  const REQUIRED = [
    "brandLine1", "brandLine2", "brandLine3", "getStarted", "haveAccount",
    "signInHeading", "signUpHeading", "name", "email", "password",
    "showPassword", "hidePassword", "forgotPassword", "submitLogin", "submitSignup",
    "switchToLogin", "switchToLoginAction", "switchToSignup", "switchToSignupAction",
    "terms", "confirmSentTitle", "confirmSentAction", "resetNeedsEmail",
  ] as const;
  const ERROR_CODES = [
    "name_required", "email_required", "email_invalid", "password_required",
    "account_exists", "invalid_credentials", "confirmation_required", "weak_password",
    "rate_limited", "network", "not_configured", "unknown",
  ] as const;

  for (const lang of ["ko", "en"] as const) {
    it(`${lang}: 모든 문구가 있고 비어 있지 않다`, () => {
      const a = memorialT(lang).auth as Record<string, unknown>;
      for (const key of REQUIRED) {
        assert.equal(typeof a[key], "string", `${lang}.auth.${key}`);
        assert.ok((a[key] as string).trim().length > 0, `${lang}.auth.${key} 가 비었다`);
      }
      const errors = a.errors as Record<string, string>;
      for (const code of ERROR_CODES) {
        assert.ok(errors[code]?.trim().length > 0, `${lang}.auth.errors.${code}`);
      }
      assert.ok(memorialT(lang).auth.passwordTooShort(6).includes("6"));
      assert.ok(memorialT(lang).auth.confirmSentBody("x@y.zz").includes("x@y.zz"));
      assert.ok(memorialT(lang).auth.resetSent("x@y.zz").includes("x@y.zz"));
    });
  }

  it("브랜드 위계 (en) 는 승인된 세 줄이다", () => {
    const a = memorialT("en").auth;
    assert.equal(a.brandLine1, "Their love lives on.");
    assert.equal(a.brandLine2, "Always with you.");
    assert.equal(a.brandLine3, "Create. Preserve. Relive.");
  });

  it("폼 위에 떠 있는 언어 토글이 없다 — Get Started 헤더 구석 한 곳뿐", () => {
    assert.doesNotMatch(auth, /LanguageToggle/);
    assert.match(start, /<LanguageToggle language=\{language\} onChange=\{onLanguageChange\} className="eb-entry__lang" \/>/);
  });
});

describe("12. 예전 반려 이름이 없어도 아무것도 깨지지 않는다", () => {
  it("pet-profile 은 값이 없으면 빈 문자열 — 예전 저장값은 그대로 읽힌다(무해)", () => {
    assert.match(petProfile, /localStorage\.getItem\(PET_NAME_KEY\)\?\.trim\(\) \?\? ""/);
  });

  it("홈은 이름이 없으면 자리 번호 라벨로 대체하고 인사말은 userName 이 있을 때만 그린다", () => {
    assert.match(home, /petName\?\.trim\(\) \? petName\.trim\(\) : texts\.petLabel\(index \+ 1\)/);
    assert.match(home, /\{userName \? \(/);
    assert.match(app, /petName=\{getPetName\(\) \|\| undefined\}/);
  });

  it("기기 호출 이름 동기화는 계정 인증이 아니라 펫/기기 흐름의 것이다", () => {
    assert.match(petProfile, /export async function syncPetProfileToDevice/);
    assert.doesNotMatch(auth, /syncPetProfileToDevice/);
  });
});
