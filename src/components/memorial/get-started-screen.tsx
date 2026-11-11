"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { LanguageToggle } from "./language-toggle";

interface GetStartedScreenProps {
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  /** Primary — Get Started → Sign Up. */
  onGetStarted: () => void;
  /** Secondary — I already have an account → Sign In. */
  onSignIn: () => void;
}

/**
 * 첫 방문(세션 없음) 진입 화면.
 *
 * SoulTrace 페이퍼 레이아웃: 언어 토글 → 라운드 히어로 → 세리프 문장 →
 * 포레스트 CTA / 크림 아웃라인 로그인. 자동 전환은 없다.
 *
 * 로그인된 세션이 있으면 이 화면은 보이지 않는다 — EternalBeamApp 의
 * 인증-인지 시작 효과가 곧장 홈으로 보낸다.
 */
export function GetStartedScreen({
  language = "ko",
  onLanguageChange,
  onGetStarted,
  onSignIn,
}: GetStartedScreenProps) {
  const a = memorialT(language).auth;

  return (
    <div data-screen="getStarted" className="eb-entry eb-entry--start eb-paper h-full min-h-0">
      <div className="eb-entry__column">
        <header className="eb-entry__top">
          <img
            src="/eternal-beam-logo-splash.png?v=1"
            alt="Eternal Beam"
            className="eb-entry__logo"
            draggable={false}
          />
          {onLanguageChange ? (
            <LanguageToggle language={language} onChange={onLanguageChange} className="eb-entry__lang" />
          ) : null}
        </header>

        <div className="eb-entry__art" aria-hidden>
          <picture>
            <source media="(min-width: 1024px)" srcSet="/auth/hero-landscape.jpg" />
            <img
              src="/auth/hero-portrait.jpg"
              alt=""
              className="eb-entry__art-img"
              draggable={false}
              decoding="async"
            />
          </picture>
        </div>

        <div className="eb-entry__content">
          <h1 className="eb-entry__statement">
            <span className="eb-entry__line eb-entry__line--1">{a.brandLine1}</span>
            <span className="eb-entry__line eb-entry__line--2">{a.brandLine2}</span>
          </h1>
          <p className="eb-entry__tagline">{a.brandLine3}</p>

          <div className="eb-entry__actions">
            <button
              type="button"
              onClick={onGetStarted}
              className="eb-btn eb-btn--primary mem-btn-primary eb-entry__cta"
              data-action="get-started"
            >
              <span className="memorial-btn-label">{a.getStarted}</span>
            </button>
            <button
              type="button"
              onClick={onSignIn}
              className="eb-btn eb-btn--secondary eb-entry__cta eb-entry__cta--secondary"
              data-action="sign-in"
            >
              {a.haveAccount}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
