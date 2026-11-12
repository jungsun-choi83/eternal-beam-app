"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { LanguageToggle } from "./language-toggle";

const MARK_SRC = "/brand/eternal-beam-mark.png";

interface GetStartedScreenProps {
  language?: string;
  onLanguageChange?: (lang: "ko" | "en") => void;
  /** Primary — Get Started → Sign Up. */
  onGetStarted: () => void;
  /** Secondary — I already have an account → Sign In. */
  onSignIn: () => void;
}

/**
 * 스플래시 다음 진입 화면.
 * 로고 · 둥근 과슈 장면 · 카피 · 시작하기 · 밑줄 계정 링크.
 * 그림 위에 글씨를 올리지 않는다.
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
      <header className="eb-entry__chrome">
        <span aria-hidden />
        {onLanguageChange ? (
          <LanguageToggle language={language} onChange={onLanguageChange} className="eb-entry__lang" />
        ) : null}
      </header>

      <div className="eb-entry__brand">
        <img
          src={MARK_SRC}
          alt=""
          width={465}
          height={316}
          className="eb-entry__mark"
          draggable={false}
        />
        <p className="eb-entry__wordmark">Eternal Beam</p>
      </div>

      <div className="eb-entry__scene" aria-hidden>
        <img
          src="/auth/entry-meadow.jpg?v=1"
          alt=""
          className="eb-entry__scene-img"
          draggable={false}
          decoding="async"
        />
      </div>

      <div className="eb-entry__identity">
        <h1 className="eb-entry__statement">
          <span className="eb-entry__line eb-entry__line--1">{a.getStartedHeadline1}</span>
          <span className="eb-entry__line eb-entry__line--2">{a.getStartedHeadline2}</span>
        </h1>
        <p className="eb-entry__tagline">{a.getStartedLead}</p>
      </div>

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
          className="eb-entry__account"
          data-action="sign-in"
        >
          {a.haveAccount}
        </button>
      </div>
    </div>
  );
}
