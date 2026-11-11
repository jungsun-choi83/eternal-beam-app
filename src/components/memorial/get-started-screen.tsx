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
 * SoulTrace 웰컴 레이아웃: 풀블리드 장면 위에 얇은 브랜드 바,
 * 아래 크림 플레이트에 세리프 문장과 포레스트 CTA.
 * 자동 전환은 없다.
 */
export function GetStartedScreen({
  language = "ko",
  onLanguageChange,
  onGetStarted,
  onSignIn,
}: GetStartedScreenProps) {
  const a = memorialT(language).auth;

  return (
    <div data-screen="getStarted" className="eb-entry eb-entry--start h-full min-h-0">
      <div className="eb-entry__scene" aria-hidden>
        <picture>
          <source media="(min-width: 1024px)" srcSet="/auth/hero-landscape.jpg" />
          <img
            src="/auth/hero-portrait.jpg"
            alt=""
            className="eb-entry__scene-img"
            draggable={false}
            decoding="async"
          />
        </picture>
        <div className="eb-entry__scene-veil" />
      </div>

      <header className="eb-entry__chrome">
        <span className="eb-entry__identity">
          <img
            src="/eternal-beam-logo-symbol.png"
            alt=""
            className="eb-entry__mark"
            draggable={false}
          />
          <span className="eb-entry__wordmark">Eternal Beam</span>
        </span>
        {onLanguageChange ? (
          <LanguageToggle language={language} onChange={onLanguageChange} className="eb-entry__lang" />
        ) : null}
      </header>

      <section className="eb-entry__plate">
        <p className="eb-entry__eyebrow">Eternal Beam</p>
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
      </section>
    </div>
  );
}
