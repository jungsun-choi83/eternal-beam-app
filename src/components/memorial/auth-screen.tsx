"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Mail, Lock, User, Eye, EyeOff, ArrowRight, Heart } from "lucide-react";
import { HolographicBackground } from "./holographic-background";
import { HologramEffects } from "./hologram-effects";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { setEternalBeamUserId } from "@/lib/eternal-beam-user";
import {
  isSupabaseAuthConfigured,
  signInWithPassword,
  signUpWithPassword,
  syncEternalBeamIdentity,
} from "@/lib/supabase-auth";
import { getPetName, setPetName, syncPetProfileToDevice } from "@/lib/pet-profile";

interface AuthScreenProps {
  initialMode?: "login" | "signup";
  /** QR 직후 등 — 로그인/회원가입 탭 전환 숨김 */
  lockMode?: "login" | "signup";
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  /**
   * 확인 메일이 돌아올 절대 URL. 넘기지 않으면 현재 origin 을 쓴다
   * (supabase-auth.defaultEmailRedirectTo). Soul Trace 가져오기처럼 "돌아와서
   * 이어서 할 일이 있는" 진입은 자기 경로를 명시한다.
   */
  emailRedirectTo?: string;
  onAuthComplete: (userName?: string) => void;
}

const inputClass =
  "w-full py-3.5 pl-12 pr-4 rounded-xl text-sm font-medium outline-none transition-all duration-300 placeholder:text-[#4A4A4A]";

function fieldStyle(focused: boolean) {
  return {
    background: "rgba(0, 0, 0, 0.4)",
    border: focused ? "1px solid rgba(201, 162, 39, 0.5)" : "1px solid rgba(255, 255, 255, 0.08)",
    color: "#F5F5F7",
    boxShadow: focused ? "0 0 20px rgba(201, 162, 39, 0.2), inset 0 0 20px rgba(201, 162, 39, 0.05)" : "none",
  } as const;
}

export function AuthScreen({
  initialMode = "login",
  lockMode,
  language = "ko",
  emailRedirectTo,
  onAuthComplete,
}: AuthScreenProps) {
  const a = memorialT(language).auth;
  const [mode, setMode] = useState<"login" | "signup">(lockMode ?? initialMode);
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [petName, setPetNameField] = useState(() =>
    typeof window !== "undefined" ? getPetName() : ""
  );
  const [petNameError, setPetNameError] = useState<string | null>(null);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [focusedField, setFocusedField] = useState<string | null>(null);

  const isSignupFlow = lockMode === "signup" || mode === "signup";
  const pageTitle = lockMode === "signup" ? a.signUp : lockMode === "login" ? a.signIn : mode === "login" ? a.signIn : a.signUp;

  const handleSubmit = async () => {
    if (mode === "signup" && !petName.trim()) {
      setPetNameError(a.petNameRequired);
      return;
    }
    setPetNameError(null);
    setAuthError(null);
    setIsLoading(true);

    // ── 실제 인증 ────────────────────────────────────────────────────────────
    // 예전에는 1.5초 대기 후 localStorage 에 이메일을 쓰는 것이 전부였고
    // (비밀번호는 쓰이지도 않았다), 그래서 프리미엄 API 에 보낼 토큰이 없었다.
    //
    // Supabase 가 설정돼 있으면 진짜로 로그인한다. 설정돼 있지 않으면(로컬 개발
    // 환경 등) 예전 동작을 그대로 유지한다 — 여기서 막으면 인증과 무관한 기존
    // 플로우(무료 BREATHING 포함)가 전부 멈춘다.
    let signedIn = false;
    if (isSupabaseAuthConfigured() && email.trim() && password) {
      const r =
        mode === "signup"
          ? await signUpWithPassword(email, password, { emailRedirectTo })
          : await signInWithPassword(email, password);
      if (!r.ok) {
        setIsLoading(false);
        setAuthError(r.message);
        return;
      }
      signedIn = !r.needsEmailConfirmation;
      if (r.needsEmailConfirmation) {
        setIsLoading(false);
        setAuthError(a.confirmEmailSent);
        return;
      }
    }

    if (mode === "signup" && petName.trim()) {
      setPetName(petName.trim());
      void syncPetProfileToDevice();
    }

    const label = (name || email.split("@")[0] || "").trim();
    // 로컬 신원은 잠정값이다. 로그인했다면 **서버가 확정한 신원**으로 덮어쓴다 —
    // 검증된 이메일이면 소문자 이메일이 그대로 나오므로 기존 데이터가 유지되고,
    // 아니면 새 신원이 온다. 어느 쪽이든 프리미엄과 지갑이 같은 값을 쓰게 된다.
    if (email.trim()) {
      setEternalBeamUserId(email.trim().toLowerCase());
    } else if (label) {
      setEternalBeamUserId(label);
    }
    if (signedIn) {
      await syncEternalBeamIdentity();
    }

    setIsLoading(false);
    onAuthComplete(label || undefined);
  };

  return (
    <div className="auth-screen-shell hologram-bg-active h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      <header className="auth-screen-header shrink-0 relative z-10">
        <h1 className="screen-title text-center text-xl m-0" style={{ color: "#F5F5F7" }}>
          {pageTitle}
        </h1>
      </header>

      <div className="auth-screen-body flex-1 min-h-0 overflow-y-auto hide-scrollbar px-5 py-2 relative z-10">
        <div className="auth-form-card mx-auto w-full max-w-[340px]">
          {!lockMode ? (
            <div
              className="flex rounded-2xl p-1.5 mb-5"
              style={{
                background: "rgba(0, 0, 0, 0.4)",
                border: "1px solid rgba(255, 255, 255, 0.06)",
              }}
            >
              {(["login", "signup"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className="flex-1 py-3 rounded-xl text-sm font-semibold tracking-wide relative"
                  style={{ color: mode === m ? "#F5F5F7" : "#6B6B6B" }}
                >
                  {mode === m ? (
                    <span
                      className="absolute inset-0 rounded-xl"
                      style={{
                        background: "linear-gradient(135deg, rgba(201, 162, 39, 0.2) 0%, rgba(184, 134, 11, 0.15) 100%)",
                        border: "1px solid rgba(201, 162, 39, 0.3)",
                      }}
                    />
                  ) : null}
                  <span className="relative z-10">{m === "login" ? a.signIn : a.signUp}</span>
                </button>
              ))}
            </div>
          ) : null}

          <div className="space-y-3">
            {isSignupFlow ? (
              <div className="relative">
                <User
                  className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
                  style={{ color: focusedField === "name" ? "#c9a227" : "#6B6B6B" }}
                  strokeWidth={1.5}
                />
                <input
                  type="text"
                  placeholder={a.name}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  onFocus={() => setFocusedField("name")}
                  onBlur={() => setFocusedField(null)}
                  className={inputClass}
                  style={fieldStyle(focusedField === "name")}
                />
              </div>
            ) : null}

            {isSignupFlow ? (
              <div>
                <div className="relative">
                  <Heart
                    className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
                    style={{ color: focusedField === "petName" ? "#c9a227" : "#6B6B6B" }}
                    strokeWidth={1.5}
                  />
                  <input
                    type="text"
                    placeholder={a.petNamePlaceholder}
                    value={petName}
                    onChange={(e) => {
                      setPetNameField(e.target.value);
                      if (petNameError) setPetNameError(null);
                    }}
                    onFocus={() => setFocusedField("petName")}
                    onBlur={() => setFocusedField(null)}
                    className={inputClass}
                    style={fieldStyle(petNameError !== null || focusedField === "petName")}
                    autoComplete="off"
                  />
                </div>
                <p className="auth-field-hint mt-1.5 px-1">{a.petNameHint}</p>
                {petNameError ? <p className="auth-field-error mt-1 px-1">{petNameError}</p> : null}

              </div>
            ) : null}

            <div className="relative">
              <Mail
                className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
                style={{ color: focusedField === "email" ? "#c9a227" : "#6B6B6B" }}
                strokeWidth={1.5}
              />
              <input
                type="email"
                placeholder={a.email}
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                onFocus={() => setFocusedField("email")}
                onBlur={() => setFocusedField(null)}
                className={inputClass}
                style={fieldStyle(focusedField === "email")}
              />
            </div>

            <div className="relative">
              <Lock
                className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5"
                style={{ color: focusedField === "password" ? "#c9a227" : "#6B6B6B" }}
                strokeWidth={1.5}
              />
              <input
                type={showPassword ? "text" : "password"}
                placeholder={a.password}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                onFocus={() => setFocusedField("password")}
                onBlur={() => setFocusedField(null)}
                className={`${inputClass} pr-12`}
                style={fieldStyle(focusedField === "password")}
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-4 top-1/2 -translate-y-1/2"
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? (
                  <EyeOff className="w-5 h-5" style={{ color: "#6B6B6B" }} strokeWidth={1.5} />
                ) : (
                  <Eye className="w-5 h-5" style={{ color: "#6B6B6B" }} strokeWidth={1.5} />
                )}
              </button>
            </div>
          </div>

          {mode === "login" && !lockMode ? (
            <button type="button" className="text-xs mt-4 font-medium" style={{ color: "#c9a227" }}>
              {a.forgotPassword}
            </button>
          ) : null}
        </div>
      </div>
          {authError ? (
            <p className="auth-field-error mt-3 px-1 text-center">{authError}</p>
          ) : null}


      <footer className="auth-screen-footer shrink-0 relative z-10 px-5 pb-[max(1rem,env(safe-area-inset-bottom,0px))] pt-2">
        <motion.button
          type="button"
          onClick={handleSubmit}
          disabled={isLoading}
          className="w-full max-w-[340px] mx-auto py-4 rounded-2xl font-bold text-base tracking-wide flex items-center justify-center gap-2"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 8px 32px rgba(201, 162, 39, 0.3)",
          }}
          whileTap={{ scale: 0.98 }}
        >
          {isLoading ? (
            <span className="w-5 h-5 border-2 border-[#0a0a0a]/30 border-t-[#0a0a0a] rounded-full animate-spin" />
          ) : (
            <>
              <span className="text-[#0a0a0a] memorial-btn-label">
                {mode === "login" ? a.submitLogin : a.submitSignup}
              </span>
              <ArrowRight className="w-5 h-5 text-[#0a0a0a]" strokeWidth={2.5} />
            </>
          )}
        </motion.button>
        <p className="memorial-caption text-center px-2 mt-3 max-w-[340px] mx-auto" style={{ color: "#6B6B6B" }}>
          {a.terms}
        </p>
      </footer>
    </div>
  );
}
