"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { AlertTriangle, ArrowRight, CheckCircle2, Eye, EyeOff, Lock } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { clearPasswordRecoveryEntry, passwordRecoveryEntry } from "@/lib/password-recovery";
import { hasSession, onAuthStateChange, signOut, updatePassword } from "@/lib/supabase-auth";
import {
  MIN_PASSWORD_LENGTH,
  hasFieldErrors,
  validateNewPassword,
  type AuthErrorCode,
  type AuthFieldErrorCode,
  type AuthFieldErrors,
} from "@/lib/auth-form";

interface ResetPasswordScreenProps {
  language?: string;
  /** "Back to Sign In" (그만두기) 와 "Continue to Sign In" (성공 뒤) 둘 다 — 로그인 화면으로. */
  onDone: () => void;
}

// Phase 10 — inputs are brand.css .eb-input (44px, off-white, hairline);
// only the leading-icon inset is added here. (auth-screen.tsx 와 동일)
const inputClass = "eb-input w-full pl-12 pr-4 text-sm font-medium";

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

type Status = "verifying" | "ready" | "success" | "expired";

/** 세션이 서는 데 걸리는 시간의 넉넉한 상한 — supabase-js 의 해시 처리는
 * 보통 밀리초 단위지만, 그 안에 아무 세션도 서지 않으면 링크가 이미
 * 만료·소비됐다고 본다. */
const VERIFY_TIMEOUT_MS = 6000;
const VERIFY_POLL_MS = 300;

/**
 * 비밀번호 재설정 — 새 비밀번호 · 확인.
 *
 * 이 화면은 오직 하나의 진입점으로만 열린다: EternalBeamApp 의
 * resolveInitialScreen() 이 재설정 이메일 링크(URL 해시의 `type=recovery`,
 * password-recovery.ts)를 발견했을 때뿐이다. 앱 안 어떤 버튼도 여기로
 * navigateTo() 하지 않는다 — 재설정 컨텍스트 없이 이 화면에 닿을 방법이 없다.
 *
 * 링크가 세운 세션은 **재설정 전용**이다. 성공하면 로그아웃하고 로그인
 * 화면으로 돌려보낸다 — 다음 로그인이 새 비밀번호를 실제로 검증하게 만든다
 * (재설정만 하고 세션을 그대로 두면 "정말 바뀌었는지" 아무도 확인하지 않는다).
 */
export function ResetPasswordScreen({ language = "ko", onDone }: ResetPasswordScreenProps) {
  const a = memorialT(language).auth;
  const [status, setStatus] = useState<Status>(() =>
    passwordRecoveryEntry()?.kind === "recovery_error" ? "expired" : "verifying"
  );
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [fieldErrors, setFieldErrors] = useState<AuthFieldErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const firstInvalidRef = useRef<HTMLInputElement | null>(null);

  // ── 세션 확인 ────────────────────────────────────────────────────────────
  // 링크를 누른 직후라면 supabase-js 가 해시를 비동기로 처리해 세션을 세운다
  // — onAuthStateChange 로 그 순간을 잡는다. 이벤트가 우리가 구독하기 **전에**
  // 이미 지나갔을 수도 있어(늦은 구독자는 INITIAL_SESSION 만 본다) hasSession()
  // 폴링도 함께 돌린다. 정해진 시간 안에 둘 다 아무것도 못 찾으면 만료로 본다.
  useEffect(() => {
    if (status !== "verifying") return;
    let cancelled = false;
    const unsubscribe = onAuthStateChange((signedIn) => {
      if (cancelled || !signedIn) return;
      setStatus("ready");
    });
    let elapsed = 0;
    const poll = window.setInterval(() => {
      elapsed += VERIFY_POLL_MS;
      void hasSession().then((signedIn) => {
        if (cancelled) return;
        if (signedIn) {
          setStatus("ready");
        } else if (elapsed >= VERIFY_TIMEOUT_MS) {
          setStatus("expired");
        }
      });
    }, VERIFY_POLL_MS);
    return () => {
      cancelled = true;
      unsubscribe();
      window.clearInterval(poll);
    };
  }, [status]);

  const fieldMessage = (code: AuthFieldErrorCode | undefined): string | null => {
    if (!code) return null;
    if (code === "password_too_short") return a.passwordTooShort(MIN_PASSWORD_LENGTH);
    return a.errors[code];
  };

  const serverMessage = (code: AuthErrorCode, raw: string): string =>
    code === "unknown" && raw ? raw : a.errors[code];

  const handleSubmit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (isSubmitting) return;

    const errors = validateNewPassword({ password: newPassword, confirm: confirmPassword });
    setFieldErrors(errors);
    setServerError(null);
    if (hasFieldErrors(errors)) {
      firstInvalidRef.current?.focus();
      return;
    }

    setIsSubmitting(true);
    const r = await updatePassword(newPassword);
    if (!r.ok) {
      setIsSubmitting(false);
      if (r.code === "session_expired") {
        setStatus("expired");
        return;
      }
      setServerError(serverMessage(r.code, r.message));
      return;
    }

    // 재설정 전용 세션을 끝낸다 — 다음 로그인이 새 비밀번호로 실제 검증된다.
    await signOut();
    clearPasswordRecoveryEntry();
    setIsSubmitting(false);
    setStatus("success");
  };

  /** "Back to Sign In"(그만두기) / "Continue to Sign In"(성공 뒤) 공용 — 두 경우
   * 모두 재설정 세션·표식을 지우고 로그인 화면으로 보낸다. 이미 지운 뒤(성공)
   * signOut()/clear 를 다시 불러도 해가 없다. */
  const handleBackToSignIn = async () => {
    clearPasswordRecoveryEntry();
    await signOut();
    onDone();
  };

  const iconColor = (key: string, invalid: boolean) =>
    invalid ? ICON_INVALID : focusedField === key ? ICON_FOCUSED : ICON_IDLE;

  const errorId = (key: string) => `reset-${key}-error`;

  const passwordError = fieldMessage(fieldErrors.password);
  const confirmError = fieldMessage(fieldErrors.confirm);
  const firstInvalidKey = fieldErrors.password ? "password" : fieldErrors.confirm ? "confirm" : null;
  const refFor = (key: "password" | "confirm") => (key === firstInvalidKey ? firstInvalidRef : undefined);

  return (
    <div data-screen="resetPassword" data-status={status} className="eb-entry eb-entry--auth h-full min-h-0">
      {/* Desktop: brand hero on the left — same visual system as Sign Up / Sign In. */}
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
            <div className="eb-entry__compact-brand">
              <img
                src="/eternal-beam-logo-splash.png?v=1"
                alt="Eternal Beam"
                className="eb-entry__logo eb-entry__logo--compact"
                draggable={false}
              />
            </div>

            {status === "ready" ? (
              <>
                <header className="eb-entry__form-head">
                  <h1 className="eb-title eb-entry__heading m-0">{a.resetPasswordHeading}</h1>
                  <p className="eb-body-sm eb-entry__lead m-0">{a.resetPasswordLead}</p>
                </header>

                <form className="eb-entry__form" onSubmit={handleSubmit} noValidate>
                  <div className="space-y-3">
                    <div>
                      <label className="sr-only" htmlFor="reset-new-password">
                        {a.newPassword}
                      </label>
                      <div className="relative">
                        <Lock
                          className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                          style={{ color: iconColor("password", Boolean(passwordError)) }}
                          strokeWidth={1.5}
                          aria-hidden
                        />
                        <input
                          id="reset-new-password"
                          ref={refFor("password")}
                          type={showNew ? "text" : "password"}
                          name="new-password"
                          autoComplete="new-password"
                          placeholder={a.newPassword}
                          value={newPassword}
                          onChange={(e) => {
                            setNewPassword(e.target.value);
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
                        <button
                          type="button"
                          onClick={() => setShowNew((v) => !v)}
                          className="absolute right-0 top-1/2 -translate-y-1/2 w-11 h-11 flex items-center justify-center rounded-full"
                          style={{ color: ICON_IDLE }}
                          aria-label={showNew ? a.hidePassword : a.showPassword}
                          aria-pressed={showNew}
                        >
                          {showNew ? (
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

                    <div>
                      <label className="sr-only" htmlFor="reset-confirm-password">
                        {a.confirmPassword}
                      </label>
                      <div className="relative">
                        <Lock
                          className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 pointer-events-none"
                          style={{ color: iconColor("confirm", Boolean(confirmError)) }}
                          strokeWidth={1.5}
                          aria-hidden
                        />
                        <input
                          id="reset-confirm-password"
                          ref={refFor("confirm")}
                          type={showConfirm ? "text" : "password"}
                          name="confirm-password"
                          autoComplete="new-password"
                          placeholder={a.confirmPassword}
                          value={confirmPassword}
                          onChange={(e) => {
                            setConfirmPassword(e.target.value);
                            if (fieldErrors.confirm) setFieldErrors((f) => ({ ...f, confirm: undefined }));
                          }}
                          onFocus={() => setFocusedField("confirm")}
                          onBlur={() => setFocusedField(null)}
                          className={`${inputClass} pr-12`}
                          style={fieldStyle(focusedField === "confirm", Boolean(confirmError))}
                          aria-invalid={confirmError ? true : undefined}
                          aria-describedby={confirmError ? errorId("confirm") : undefined}
                          required
                        />
                        <button
                          type="button"
                          onClick={() => setShowConfirm((v) => !v)}
                          className="absolute right-0 top-1/2 -translate-y-1/2 w-11 h-11 flex items-center justify-center rounded-full"
                          style={{ color: ICON_IDLE }}
                          aria-label={showConfirm ? a.hidePassword : a.showPassword}
                          aria-pressed={showConfirm}
                        >
                          {showConfirm ? (
                            <EyeOff className="w-5 h-5" strokeWidth={1.5} aria-hidden />
                          ) : (
                            <Eye className="w-5 h-5" strokeWidth={1.5} aria-hidden />
                          )}
                        </button>
                      </div>
                      {confirmError ? (
                        <p id={errorId("confirm")} className="auth-field-error mt-1 px-1">
                          {confirmError}
                        </p>
                      ) : null}
                    </div>
                  </div>

                  {serverError ? (
                    <p className="auth-field-error eb-entry__server-error" role="alert">
                      {serverError}
                    </p>
                  ) : null}

                  <button
                    type="submit"
                    disabled={isSubmitting}
                    aria-busy={isSubmitting || undefined}
                    className="eb-btn eb-btn--primary mem-btn-primary eb-entry__cta eb-entry__submit"
                  >
                    {isSubmitting ? (
                      <span className="eb-btn__spinner" aria-hidden />
                    ) : (
                      <>
                        <span className="memorial-btn-label">{a.updatePassword}</span>
                        <ArrowRight className="w-5 h-5" strokeWidth={2.5} aria-hidden />
                      </>
                    )}
                  </button>

                  <p className="eb-entry__switch">
                    <button
                      type="button"
                      className="eb-entry__link eb-entry__link--strong"
                      onClick={() => void handleBackToSignIn()}
                      disabled={isSubmitting}
                    >
                      {a.backToSignIn}
                    </button>
                  </p>
                </form>
              </>
            ) : null}

            {status === "verifying" ? (
              <div className="eb-entry__form-head" role="status" aria-live="polite">
                <span className="eb-btn__spinner" aria-hidden style={{ color: "var(--eb-gold)" }} />
                <p className="eb-body-sm eb-entry__lead m-0">{a.resetVerifying}</p>
              </div>
            ) : null}

            {status === "success" ? (
              <div className="eb-entry__notice" role="status" aria-live="polite">
                <CheckCircle2 className="w-5 h-5 shrink-0" strokeWidth={1.75} aria-hidden />
                <div>
                  <p className="eb-entry__notice-title">{a.resetSuccessTitle}</p>
                  <p className="eb-entry__notice-body">{a.resetSuccessBody}</p>
                  <button
                    type="button"
                    className="eb-btn eb-btn--primary mem-btn-primary eb-entry__cta mt-3"
                    onClick={() => void handleBackToSignIn()}
                  >
                    {a.continueToSignIn}
                  </button>
                </div>
              </div>
            ) : null}

            {status === "expired" ? (
              <div className="eb-entry__notice" role="alert">
                <AlertTriangle className="w-5 h-5 shrink-0" strokeWidth={1.75} aria-hidden />
                <div>
                  <p className="eb-entry__notice-title">{a.resetExpiredTitle}</p>
                  <p className="eb-entry__notice-body">{a.resetExpiredBody}</p>
                  <button
                    type="button"
                    className="eb-btn eb-btn--secondary eb-entry__cta mt-3"
                    onClick={() => void handleBackToSignIn()}
                  >
                    {a.backToSignIn}
                  </button>
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}
