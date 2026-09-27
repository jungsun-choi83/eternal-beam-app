"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Mail, Lock, User, Eye, EyeOff, ArrowRight, MailCheck } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { setEternalBeamUserId } from "@/lib/eternal-beam-user";
import {
  isSupabaseAuthConfigured,
  sendPasswordReset,
  signInWithPassword,
  signUpWithPassword,
  syncEternalBeamIdentity,
} from "@/lib/supabase-auth";
import {
  MIN_PASSWORD_LENGTH,
  displayLabelFor,
  hasFieldErrors,
  normalizeEmail,
  validateLogin,
  validateSignup,
  type AuthErrorCode,
  type AuthFieldErrorCode,
  type AuthFieldErrors,
  type AuthMode,
} from "@/lib/auth-form";

interface AuthScreenProps {
  initialMode?: AuthMode;
  /** QR 직후 등 — 로그인/회원가입 전환 링크 숨김 */
  lockMode?: AuthMode;
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  /**
   * 확인 메일이 돌아올 절대 URL. 넘기지 않으면 현재 origin 을 쓴다
   * (supabase-auth.defaultEmailRedirectTo). Soul Trace 가져오기처럼 "돌아와서
   * 이어서 할 일이 있는" 진입은 자기 경로를 명시한다.
   */
  emailRedirectTo?: string;
  onAuthComplete: (userName?: string, mode?: AuthMode) => void;
}

// Phase 10 — inputs are brand.css .eb-input (44px, off-white, hairline);
// only the leading-icon inset is added here.
const inputClass = "eb-input w-full pl-12 pr-4 text-sm font-medium";

/** Focus = gold hairline + soft gold wash; invalid = terracotta. Tokens only. */
function fieldStyle(focused: boolean, invalid = false) {
  const border = invalid
    ? "1px solid var(--eb-terracotta)"
    : focused
      ? "1px solid var(--eb-gold)"
      : "1px solid var(--eb-hairline-strong)";
  const boxShadow = invalid
    ? "0 0 0 3px var(--eb-terracotta-wash)"
    : focused
      ? "0 0 0 3px var(--eb-gold-wash)"
      : "none";
  return {
    background: "var(--eb-surface)",
    border,
    color: "var(--eb-text)",
    boxShadow,
  } as const;
}

const ICON_IDLE = "var(--eb-text-3)";
const ICON_FOCUSED = "var(--eb-gold)";
const ICON_INVALID = "var(--eb-terracotta-text)";

type Notice =
  | { kind: "error"; text: string }
  | { kind: "confirm-sent"; email: string }
  | { kind: "reset-sent"; email: string };

/**
 * 계정 인증 — 회원가입(이름·이메일·비밀번호) / 로그인(이메일·비밀번호).
 *
 * 반려/기기 호출 이름은 **여기에 없다.** 예전 가입 폼이 요구하던 그 값은 계정과
 * 무관한 펫 온보딩(사진 업로드) / My Beam 기기 설정의 관심사다. 숨은 요구도,
 * 자리 채움 값("Pet 1" 같은)도 만들지 않는다.
 *
 * 레이아웃: 데스크톱은 브랜드 히어로(좌) / 폼(우) 분할, 모바일은 압축 브랜딩 +
 * 단일 컬럼 폼. CTA 는 스크롤 영역 **안**, 필드 바로 아래에 둔다 — 키보드가
 * 올라와 뷰포트가 줄어도 사용자가 스크롤해 닿을 수 있다(고정 푸터는 iOS 에서
 * 키보드 뒤로 밀려 사라진다).
 */
export function AuthScreen({
  initialMode = "login",
  lockMode,
  language = "ko",
  emailRedirectTo,
  onAuthComplete,
}: AuthScreenProps) {
  const a = memorialT(language).auth;
  const [mode, setMode] = useState<AuthMode>(lockMode ?? initialMode);
  const [showPassword, setShowPassword] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<AuthFieldErrors>({});
  const [notice, setNotice] = useState<Notice | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const firstInvalidRef = useRef<HTMLInputElement | null>(null);

  const isSignup = mode === "signup";
  const heading = isSignup ? a.signUpHeading : a.signInHeading;
  const lead = isSignup ? a.signUpLead : a.signInLead;

  // 모드가 바뀌면 이전 모드의 오류/안내는 더 이상 맞지 않는다.
  useEffect(() => {
    setFieldErrors({});
    setNotice(null);
  }, [mode]);

  const fieldMessage = (code: AuthFieldErrorCode | undefined): string | null => {
    if (!code) return null;
    if (code === "password_too_short") return a.passwordTooShort(MIN_PASSWORD_LENGTH);
    return a.errors[code];
  };

  const serverMessage = (code: AuthErrorCode, raw: string): string =>
    code === "unknown" && raw ? raw : a.errors[code];

  const switchMode = (next: AuthMode) => {
    if (lockMode) return;
    setMode(next);
  };

  const handleSubmit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (isLoading) return;

    // ── 검증: 빈/잘못된 자격 증명은 여기서 멈춘다 — 서버에도, 앱 안에도 못 들어간다.
    const errors = isSignup
      ? validateSignup({ name, email, password })
      : validateLogin({ email, password });
    setFieldErrors(errors);
    setNotice(null);
    if (hasFieldErrors(errors)) {
      firstInvalidRef.current?.focus();
      return;
    }

    setIsLoading(true);
    const trimmedName = name.trim();
    const normalizedEmail = normalizeEmail(email);

    // ── 실제 인증 ────────────────────────────────────────────────────────────
    // Supabase 가 설정돼 있으면 진짜로 로그인한다. 설정돼 있지 않으면(로컬 개발
    // 환경 등) 로컬 신원만 세운다 — 여기서 막으면 인증과 무관한 기존 플로우
    // (무료 BREATHING 포함)가 전부 멈춘다. 빈 자격 증명은 위 검증이 이미 막았다.
    let signedIn = false;
    if (isSupabaseAuthConfigured()) {
      const r = isSignup
        ? await signUpWithPassword(normalizedEmail, password, { emailRedirectTo, name: trimmedName })
        : await signInWithPassword(normalizedEmail, password);
      if (!r.ok) {
        setIsLoading(false);
        setNotice({ kind: "error", text: serverMessage(r.code, r.message) });
        return;
      }
      if (r.needsEmailConfirmation) {
        // 확인 메일이 켜진 프로젝트 — 토큰이 없으니 앱에 들이지 않는다. 링크를
        // 누르고 돌아오면 세션 구독(onAuthStateChange)이 나머지를 잇는다.
        setIsLoading(false);
        setNotice({ kind: "confirm-sent", email: normalizedEmail });
        return;
      }
      signedIn = true;
    }

    const label = displayLabelFor(trimmedName, normalizedEmail);
    // 로컬 신원은 잠정값이다. 로그인했다면 **서버가 확정한 신원**으로 덮어쓴다 —
    // 검증된 이메일이면 소문자 이메일이 그대로 나오므로 기존 데이터가 유지되고,
    // 아니면 새 신원이 온다. 어느 쪽이든 프리미엄과 지갑이 같은 값을 쓰게 된다.
    setEternalBeamUserId(normalizedEmail);
    if (signedIn) {
      await syncEternalBeamIdentity();
    }

    setIsLoading(false);
    onAuthComplete(label || undefined, mode);
  };

  const handleForgotPassword = async () => {
    if (isLoading) return;
    const errors = validateLogin({ email, password: "x" });
    if (errors.email) {
      setFieldErrors({ email: errors.email });
      setNotice({ kind: "error", text: a.resetNeedsEmail });
      firstInvalidRef.current?.focus();
      return;
    }
    setFieldErrors({});
    setIsLoading(true);
    const normalizedEmail = normalizeEmail(email);
    const r = await sendPasswordReset(normalizedEmail);
    setIsLoading(false);
    if (!r.ok) {
      setNotice({ kind: "error", text: serverMessage(r.code, r.message) });
      return;
    }
    setNotice({ kind: "reset-sent", email: normalizedEmail });
  };

  const iconColor = (key: string, invalid: boolean) =>
    invalid ? ICON_INVALID : focusedField === key ? ICON_FOCUSED : ICON_IDLE;

  const errorId = (key: string) => `auth-${key}-error`;

  const nameError = fieldMessage(fieldErrors.name);
  const emailError = fieldMessage(fieldErrors.email);
  const passwordError = fieldMessage(fieldErrors.password);
  const firstInvalidKey = fieldErrors.name ? "name" : fieldErrors.email ? "email" : fieldErrors.password ? "password" : null;
  const refFor = (key: "name" | "email" | "password") => (key === firstInvalidKey ? firstInvalidRef : undefined);

  return (
    <div data-screen={mode} className="eb-entry eb-entry--auth h-full min-h-0">
      {/* Desktop: brand hero on the left. Mobile: hidden — compact branding sits above the form. */}
      <div className="eb-entry__hero eb-entry__hero--desktop-only" aria-hidden>
        <img
          src="/auth/hero-landscape.jpg"
          alt=""
          className="eb-entry__hero-img"
          draggable={false}
          decoding="async"
        />
        <div className="eb-entry__hero-veil" />
        <div className="eb-entry__hero-copy">
          <img
            src="/eternal-beam-logo-splash.png?v=1"
            alt="Eternal Beam"
            className="eb-entry__logo eb-entry__logo--hero"
            draggable={false}
          />
          <p className="eb-entry__statement eb-entry__statement--hero">
            <span className="eb-entry__line eb-entry__line--1">{a.brandLine1}</span>
            <span className="eb-entry__line eb-entry__line--2">{a.brandLine2}</span>
          </p>
          <p className="eb-entry__tagline eb-entry__tagline--hero">{a.brandLine3}</p>
        </div>
      </div>

      <div className="eb-entry__panel eb-entry__panel--form">
        <div className="eb-entry__form-scroll hide-scrollbar">
          <div className="eb-entry__form-col">
            {/* Mobile-only compact branding. */}
            <div className="eb-entry__compact-brand">
              <img
                src="/eternal-beam-logo-splash.png?v=1"
                alt="Eternal Beam"
                className="eb-entry__logo eb-entry__logo--compact"
                draggable={false}
              />
              <p className="eb-entry__tagline eb-entry__tagline--compact">
                {a.brandLine1} {a.brandLine2}
              </p>
            </div>

            <header className="eb-entry__form-head">
              <h1 className="eb-title eb-entry__heading m-0">{heading}</h1>
              <p className="eb-body-sm eb-entry__lead m-0">{lead}</p>
            </header>

            {notice?.kind === "confirm-sent" ? (
              <div className="eb-entry__notice" role="status" aria-live="polite">
                <MailCheck className="w-5 h-5 shrink-0" strokeWidth={1.75} aria-hidden />
                <div>
                  <p className="eb-entry__notice-title">{a.confirmSentTitle}</p>
                  <p className="eb-entry__notice-body">{a.confirmSentBody(notice.email)}</p>
                  {!lockMode ? (
                    <button
                      type="button"
                      className="eb-entry__link mt-2"
                      onClick={() => {
                        setMode("login");
                      }}
                    >
                      {a.confirmSentAction}
                    </button>
                  ) : null}
                </div>
              </div>
            ) : null}

            <form className="eb-entry__form" onSubmit={handleSubmit} noValidate>
              <div className="space-y-3">
                {isSignup ? (
                  <div>
                    <label className="sr-only" htmlFor="auth-name">
                      {a.name}
                    </label>
                    <div className="relative">
                      <User
                        className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                        style={{ color: iconColor("name", Boolean(nameError)) }}
                        strokeWidth={1.5}
                        aria-hidden
                      />
                      <input
                        id="auth-name"
                        ref={refFor("name")}
                        type="text"
                        name="name"
                        autoComplete="name"
                        placeholder={a.name}
                        value={name}
                        onChange={(e) => {
                          setName(e.target.value);
                          if (fieldErrors.name) setFieldErrors((f) => ({ ...f, name: undefined }));
                        }}
                        onFocus={() => setFocusedField("name")}
                        onBlur={() => setFocusedField(null)}
                        className={inputClass}
                        style={fieldStyle(focusedField === "name", Boolean(nameError))}
                        aria-invalid={nameError ? true : undefined}
                        aria-describedby={nameError ? errorId("name") : undefined}
                        required
                      />
                    </div>
                    {nameError ? (
                      <p id={errorId("name")} className="auth-field-error mt-1 px-1">
                        {nameError}
                      </p>
                    ) : null}
                  </div>
                ) : null}

                <div>
                  <label className="sr-only" htmlFor="auth-email">
                    {a.email}
                  </label>
                  <div className="relative">
                    <Mail
                      className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                      style={{ color: iconColor("email", Boolean(emailError)) }}
                      strokeWidth={1.5}
                      aria-hidden
                    />
                    <input
                      id="auth-email"
                      ref={refFor("email")}
                      type="email"
                      name="email"
                      inputMode="email"
                      autoComplete="email"
                      autoCapitalize="none"
                      spellCheck={false}
                      placeholder={a.email}
                      value={email}
                      onChange={(e) => {
                        setEmail(e.target.value);
                        if (fieldErrors.email) setFieldErrors((f) => ({ ...f, email: undefined }));
                      }}
                      onFocus={() => setFocusedField("email")}
                      onBlur={() => setFocusedField(null)}
                      className={inputClass}
                      style={fieldStyle(focusedField === "email", Boolean(emailError))}
                      aria-invalid={emailError ? true : undefined}
                      aria-describedby={emailError ? errorId("email") : undefined}
                      required
                    />
                  </div>
                  {emailError ? (
                    <p id={errorId("email")} className="auth-field-error mt-1 px-1">
                      {emailError}
                    </p>
                  ) : null}
                </div>

                <div>
                  <label className="sr-only" htmlFor="auth-password">
                    {a.password}
                  </label>
                  <div className="relative">
                    <Lock
                      className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                      style={{ color: iconColor("password", Boolean(passwordError)) }}
                      strokeWidth={1.5}
                      aria-hidden
                    />
                    <input
                      id="auth-password"
                      ref={refFor("password")}
                      type={showPassword ? "text" : "password"}
                      name="password"
                      autoComplete={isSignup ? "new-password" : "current-password"}
                      placeholder={a.password}
                      value={password}
                      onChange={(e) => {
                        setPassword(e.target.value);
                        if (fieldErrors.password) setFieldErrors((f) => ({ ...f, password: undefined }));
                      }}
                      onFocus={() => setFocusedField("password")}
                      onBlur={() => setFocusedField(null)}
                      className={`${inputClass} pr-12`}
                      style={fieldStyle(focusedField === "password", Boolean(passwordError))}
                      aria-invalid={passwordError ? true : undefined}
                      aria-describedby={passwordError ? errorId("password") : undefined}
                      required
                    />
                    {/* 44px hit area, sitting inside the field's right padding. */}
                    <button
                      type="button"
                      onClick={() => setShowPassword((v) => !v)}
                      className="absolute right-0 top-1/2 -translate-y-1/2 w-11 h-11 flex items-center justify-center rounded-full"
                      style={{ color: ICON_IDLE }}
                      aria-label={showPassword ? a.hidePassword : a.showPassword}
                      aria-pressed={showPassword}
                    >
                      {showPassword ? (
                        <EyeOff className="w-5 h-5" strokeWidth={1.5} aria-hidden />
                      ) : (
                        <Eye className="w-5 h-5" strokeWidth={1.5} aria-hidden />
                      )}
                    </button>
                  </div>
                  {passwordError ? (
                    <p id={errorId("password")} className="auth-field-error mt-1 px-1">
                      {passwordError}
                    </p>
                  ) : null}
                </div>
              </div>

              {!isSignup ? (
                <div className="eb-entry__row-end">
                  <button
                    type="button"
                    className="eb-entry__link"
                    onClick={() => void handleForgotPassword()}
                    disabled={isLoading}
                  >
                    {a.forgotPassword}
                  </button>
                </div>
              ) : null}

              {notice?.kind === "error" ? (
                <p className="auth-field-error eb-entry__server-error" role="alert">
                  {notice.text}
                </p>
              ) : null}
              {notice?.kind === "reset-sent" ? (
                <p className="eb-entry__ok" role="status" aria-live="polite">
                  {a.resetSent(notice.email)}
                </p>
              ) : null}

              <button
                type="submit"
                disabled={isLoading}
                aria-busy={isLoading || undefined}
                className="eb-btn eb-btn--primary mem-btn-primary eb-entry__cta eb-entry__submit"
              >
                {isLoading ? (
                  <span className="eb-btn__spinner" aria-hidden />
                ) : (
                  <>
                    <span className="memorial-btn-label">{isSignup ? a.submitSignup : a.submitLogin}</span>
                    <ArrowRight className="w-5 h-5" strokeWidth={2.5} aria-hidden />
                  </>
                )}
              </button>

              {!lockMode ? (
                <p className="eb-entry__switch">
                  {isSignup ? a.switchToLogin : a.switchToSignup}{" "}
                  <button
                    type="button"
                    className="eb-entry__link eb-entry__link--strong"
                    onClick={() => switchMode(isSignup ? "login" : "signup")}
                  >
                    {isSignup ? a.switchToLoginAction : a.switchToSignupAction}
                  </button>
                </p>
              ) : null}

              <p className="memorial-caption eb-entry__terms">{a.terms}</p>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
