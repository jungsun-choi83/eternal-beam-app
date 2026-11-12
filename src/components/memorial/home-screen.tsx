"use client";

import { memorialT } from "@/components/memorial/memorial-i18n";
import { Settings } from "lucide-react";
import { LanguageToggle } from "./language-toggle";
import { LivingHero } from "./living-hero";

/**
 * Permanent brand landscape — the approved golden-retriever painting.
 * Uploading or switching pets never replaces it.
 */
const HOME_HERO_IMAGE = "/auth/entry-meadow.jpg";
const HOME_MARK_SRC = "/brand/eternal-beam-mark.png";

export interface HomePetSummary {
  index: number;
  previewImage: string | null;
  themeId?: number | null;
}

interface HomeScreenProps {
  petName?: string;
  pets: HomePetSummary[];
  activePetIndex: number;
  userName?: string;
  language?: string;
  onMediaFile: (file: File) => void;
  onSelectPet: (index: number) => void;
  onAddPet: () => void;
  onLibrary?: () => void;
  onDevice?: () => void;
  onRegisterDevice?: () => void;
  onSettings?: () => void;
  onLanguageChange?: (lang: "ko" | "en") => void;
  onTryForest?: () => void;
  onSaveToNFC: () => void;
  onSignIn?: () => void;
}

export function HomeScreen({
  language = "ko",
  onSettings,
  onLanguageChange,
  onTryForest,
  onSaveToNFC,
  onSignIn,
}: HomeScreenProps) {
  const texts = memorialT(language).home;

  return (
    <div className="eb-paper eb-home memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <header className="eb-home__top relative z-10 shrink-0">
        <div className="eb-home__header-tools">
          {onLanguageChange ? (
            <LanguageToggle language={language} onChange={onLanguageChange} className="eb-entry__lang" />
          ) : null}
          <button
            type="button"
            onClick={onSettings}
            className="mem-icon-btn eb-home__settings"
            aria-label="Settings"
          >
            <Settings className="w-5 h-5" />
          </button>
        </div>
      </header>

      <div className="eb-home__scroll flex-1 overflow-y-auto min-h-0 relative z-10">
        <div className="eb-home__content">
          <section className="eb-home__stage" aria-labelledby="eb-home-hero-headline">
            <div className="eb-home__welcome">
            <div className="eb-home__brandcenter">
              <img
                src={HOME_MARK_SRC}
                alt=""
                width={465}
                height={316}
                className="eb-home__mark"
                draggable={false}
              />
              <p className="eb-home__wordmark">{texts.heroEyebrow}</p>
            </div>

            <div className="eb-home__landscape">
              <LivingHero src={HOME_HERO_IMAGE} alt={texts.heroImageAlt} />
            </div>

            <div className="eb-home__copy">
              <h1 id="eb-home-hero-headline" className="eb-home__hero-headline">
                {texts.heroHeadline}
              </h1>
              <p className="eb-home__hero-copy">{texts.heroBody}</p>

              <button type="button" onClick={onSaveToNFC} className="eb-home__hero-cta">
                {texts.heroCtaCreate}
              </button>

              {onSignIn ? (
                <button type="button" onClick={onSignIn} className="eb-home__account">
                  {texts.heroCtaLibrary}
                </button>
              ) : null}
            </div>
            </div>

              {onTryForest ? (
                <button type="button" onClick={onTryForest} className="eb-home__text-link">
                  {texts.tryForest}
                </button>
              ) : null}
          </section>
        </div>
      </div>
    </div>
  );
}
