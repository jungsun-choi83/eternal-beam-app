import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  MIN_PASSWORD_LENGTH,
  classifyAuthError,
  displayLabelFor,
  hasFieldErrors,
  isValidEmail,
  normalizeEmail,
  validateLogin,
  validateNewPassword,
  validateSignup,
} from "./auth-form.ts";

describe("가입 검증 — 이름 · 이메일 · 비밀번호가 전부다", () => {
  it("세 값만 있으면 통과한다 (반려/기기 이름 없음)", () => {
    const errors = validateSignup({ name: "Susan", email: "susan@example.com", password: "hunter22" });
    assert.deepEqual(errors, {});
    assert.equal(hasFieldErrors(errors), false);
  });

  it("입력 계약에 반려/기기 호출 이름 칸이 없다 — 숨은 요구도 없다", () => {
    // 여분의 키를 넣어도 무시된다: 계정 만들기는 그 값을 알지 못한다.
    const errors = validateSignup({
      name: "Susan",
      email: "susan@example.com",
      password: "hunter22",
      ...({ petName: "" } as object),
    });
    assert.deepEqual(errors, {});
  });

  it("이름이 비면 막는다", () => {
    assert.equal(validateSignup({ name: "   ", email: "a@b.co", password: "hunter22" }).name, "name_required");
  });

  it("이메일이 비거나 형태가 틀리면 막는다", () => {
    assert.equal(validateSignup({ name: "S", email: "", password: "hunter22" }).email, "email_required");
    assert.equal(validateSignup({ name: "S", email: "not-an-email", password: "hunter22" }).email, "email_invalid");
    assert.equal(validateSignup({ name: "S", email: "a@b", password: "hunter22" }).email, "email_invalid");
  });

  it("비밀번호는 비어 있거나 프로젝트 최소 길이 미만이면 막는다 — 더 강한 규칙은 만들지 않는다", () => {
    assert.equal(validateSignup({ name: "S", email: "a@b.co", password: "" }).password, "password_required");
    assert.equal(
      validateSignup({ name: "S", email: "a@b.co", password: "x".repeat(MIN_PASSWORD_LENGTH - 1) }).password,
      "password_too_short",
    );
    assert.equal(validateSignup({ name: "S", email: "a@b.co", password: "x".repeat(MIN_PASSWORD_LENGTH) }).password, undefined);
    assert.equal(MIN_PASSWORD_LENGTH, 6, "Supabase Auth 기본 최소 길이와 같아야 한다");
  });

  it("빈 폼은 세 칸 모두 오류다 — 아무것도 앱에 들어가지 못한다", () => {
    const errors = validateSignup({ name: "", email: "", password: "" });
    assert.deepEqual(errors, { name: "name_required", email: "email_required", password: "password_required" });
    assert.equal(hasFieldErrors(errors), true);
  });
});

describe("로그인 검증 — 이메일 · 비밀번호", () => {
  it("기존 사용자는 이메일과 비밀번호만으로 통과한다 (이름·반려 이름 불필요)", () => {
    assert.deepEqual(validateLogin({ email: "old.user@example.com", password: "legacy-pass" }), {});
  });

  it("빈/잘못된 자격 증명은 막는다", () => {
    assert.deepEqual(validateLogin({ email: "", password: "" }), { email: "email_required", password: "password_required" });
    assert.equal(validateLogin({ email: "nope", password: "x" }).email, "email_invalid");
    assert.equal(validateLogin({ email: "a@b.co", password: "" }).password, "password_required");
  });
});

describe("이메일 정규화 / 표시 이름", () => {
  it("이메일은 공백 제거 + 소문자 — 서버 신원(소문자 이메일)과 같은 규칙", () => {
    assert.equal(normalizeEmail("  Susan@Example.COM "), "susan@example.com");
    assert.equal(isValidEmail(" susan@example.com "), true);
  });

  it("표시 이름은 이름 우선, 없으면 이메일 앞부분, 그것도 없으면 빈 문자열", () => {
    assert.equal(displayLabelFor("  Susan ", "susan@example.com"), "Susan");
    assert.equal(displayLabelFor("", "susan@example.com"), "susan");
    assert.equal(displayLabelFor("", ""), "");
  });
});

describe("서버 오류 분류 — 화면 문구 키", () => {
  it("이미 있는 계정", () => {
    assert.equal(classifyAuthError("User already registered").code, "account_exists");
  });
  it("잘못된 자격 증명", () => {
    assert.equal(classifyAuthError("Invalid login credentials").code, "invalid_credentials");
  });
  it("이메일 확인 필요", () => {
    assert.equal(classifyAuthError("Email not confirmed").code, "confirmation_required");
  });
  it("약한 비밀번호", () => {
    assert.equal(classifyAuthError("Password should be at least 6 characters.").code, "weak_password");
    assert.equal(classifyAuthError("Password is known to be weak and easy to guess").code, "weak_password");
  });
  it("네트워크 실패 (fetch 가 던진 TypeError 포함)", () => {
    assert.equal(classifyAuthError("Failed to fetch", { name: "TypeError" }).code, "network");
    assert.equal(classifyAuthError("Load failed").code, "network");
    assert.equal(classifyAuthError("", { name: "AuthRetryableFetchError" }).code, "network");
  });
  it("속도 제한", () => {
    assert.equal(classifyAuthError("Email rate limit exceeded").code, "rate_limited");
    assert.equal(classifyAuthError("boom", { status: 429 }).code, "rate_limited");
  });
  it("모르는 문구는 unknown + 원문", () => {
    const c = classifyAuthError("Something odd");
    assert.equal(c.code, "unknown");
    assert.equal(c.raw, "Something odd");
  });
});

describe("새 비밀번호 검증 — 비밀번호 재설정 화면", () => {
  it("일치하는 유효한 비밀번호는 통과한다", () => {
    assert.deepEqual(validateNewPassword({ password: "hunter22", confirm: "hunter22" }), {});
  });

  it("둘 다 비면 둘 다 오류다", () => {
    assert.deepEqual(validateNewPassword({ password: "", confirm: "" }), {
      password: "password_required",
      confirm: "confirm_required",
    });
  });

  it("새 비밀번호가 너무 짧으면 막는다 — 회원가입과 같은 최소 길이", () => {
    const errors = validateNewPassword({ password: "x".repeat(MIN_PASSWORD_LENGTH - 1), confirm: "" });
    assert.equal(errors.password, "password_too_short");
  });

  it("서로 다르면 확인란에 불일치를 표시한다", () => {
    const errors = validateNewPassword({ password: "hunter22", confirm: "hunter23" });
    assert.deepEqual(errors, { confirm: "password_mismatch" });
  });

  it("새 비밀번호 자체가 이미 틀렸으면 확인란에는 불일치를 겹쳐 띄우지 않는다", () => {
    // confirm 이 비었으니 confirm_required 만, password_mismatch 는 아니다.
    const errors = validateNewPassword({ password: "short", confirm: "" });
    assert.equal(errors.password, "password_too_short");
    assert.equal(errors.confirm, "confirm_required");
  });

  it("빈 문자열끼리는 '일치'로 치지 않는다 — 둘 다 필수 오류로 막힌다", () => {
    const errors = validateNewPassword({ password: "", confirm: "" });
    assert.notEqual(errors.confirm, "password_mismatch");
  });
});

describe("서버 오류 분류 — 재설정 세션 만료/무효", () => {
  it("AuthSessionMissingError 이름으로 온다", () => {
    assert.equal(classifyAuthError("Auth session missing!", { name: "AuthSessionMissingError" }).code, "session_expired");
  });
  it("이름이 없어도 문구로 잡는다", () => {
    assert.equal(classifyAuthError("Auth session missing!").code, "session_expired");
  });
});
