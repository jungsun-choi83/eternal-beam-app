"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Sparkles } from "lucide-react";
import { memorialT, themeDisplayName } from "@/components/memorial/memorial-i18n";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import {
  freeMemorialThemes,
  premiumMemorialThemes,
  isPremiumTheme,
  type MemorialTheme,
} from "@/components/memorial/themes";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";
import { resolveIdleVideoUrl } from "@/app/services/videoProcessingApi";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  language?: string;
  onSelectTheme: (themeId: number) => void;
  /** requiresGeneration 테마(예: custom_photo_bg) 카드를 탭했을 때 */
  onSelectCustomBackground?: (theme: MemorialTheme) => void;
  onContinue: () => void;
  onSkip: () => void;
  onBack: () => void;
}

function ThemeCarousel({
  themes,
  selectedTheme,
  themeLabel,
  carouselRef,
  onSelect,
}: {
  themes: MemorialTheme[];
  selectedTheme: number | null;
  themeLabel: (th: MemorialTheme) => string;
  carouselRef: React.RefObject<HTMLDivElement | null>;
  onSelect: (theme: MemorialTheme) => void;
}) {
  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Previous"
        onClick={() => {
          const el = carouselRef.current;
          if (!el) return;
          el.scrollBy({ left: -Math.max(140, el.clientWidth * 0.65), behavior: "smooth" });
        }}
        className="absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-full p-2 bg-white/15 border border-white/25"
      >
        <ArrowLeft className="h-4 w-4 text-white" />
      </button>
      <button
        type="button"
        aria-label="Next"
        onClick={() => {
          const el = carouselRef.current;
          if (!el) return;
          el.scrollBy({ left: Math.max(140, el.clientWidth * 0.65), behavior: "smooth" });
        }}
        className="absolute right-0 top-1/2 z-10 -translate-y-1/2 rounded-full p-2 bg-white/15 border border-white/25"
      >
        <ArrowRight className="h-4 w-4 text-white" />
      </button>

      <div
        ref={carouselRef}
        className="hide-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto px-10 pb-2"
      >
        {themes.map((theme) => {
          const selected = selectedTheme === theme.id;
          return (
            <button
              key={theme.id}
              type="button"
              onClick={() => onSelect(theme)}
              className={`theme-selection-screen__carousel-card relative aspect-[3/4] w-[38%] shrink-0 snap-center rounded-2xl overflow-hidden border-2 transition-colors ${
                selected ? "border-[#c9a227]" : "border-transparent"
              }`}
            >
              <div
                className="absolute inset-0 bg-cover bg-center"
                style={{ backgroundImage: `url(${theme.thumb})` }}
              />
              <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-40`} />
              {theme.requiresGeneration ? (
                <div className="absolute top-2 left-2 w-5 h-5 rounded-full bg-white/15 border border-white/30 flex items-center justify-center">
                  <Sparkles className="w-3 h-3 text-[#f5d77a]" strokeWidth={2} />
                </div>
              ) : null}
              {selected ? (
                <div className="absolute top-2 right-2 w-5 h-5 rounded-full bg-[#c9a227] flex items-center justify-center">
                  <Check className="w-3 h-3 text-[#0a0a0a]" strokeWidth={3} />
                </div>
              ) : theme.premium ? (
                <div className="absolute top-2 right-2 rounded-full bg-black/50 px-1.5 py-0.5">
                  <span className="text-[8px] text-[#f5d77a] tracking-wide">{theme.price}</span>
                </div>
              ) : (
                <div className="absolute top-2 right-2 rounded-full bg-black/40 px-1.5 py-0.5">
                  <span className="text-[8px] text-[#a8e6a3] tracking-wide">FREE</span>
                </div>
              )}
              <div className="absolute bottom-2 left-0 right-0 text-center px-1">
                <span className="text-[10px] text-[#F1E5D1] tracking-wide">{themeLabel(theme)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ThemeSelectionScreen({
  cutoutImage,
  selectedTheme,
  language = "ko",
  onSelectTheme,
  onSelectCustomBackground,
  onContinue,
  onSkip,
  onBack,
}: ThemeSelectionScreenProps) {
  const tc = memorialT(language).theme;
  const themeLabel = (th: MemorialTheme) =>
    themeDisplayName(language === "ko" ? "ko" : "en", th);
  const freeCarouselRef = useRef<HTMLDivElement | null>(null);
  const premiumCarouselRef = useRef<HTMLDivElement | null>(null);
  const [idleVideoUrl, setIdleVideoUrl] = useState<string | null>(null);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (!raw) {
        setIdleVideoUrl(null);
        return;
      }
      const pipeline = JSON.parse(raw) as StoredPipeline;
      const url = resolveIdleVideoUrl(pipeline.idle_video_url);
      setIdleVideoUrl(url || null);
    } catch {
      setIdleVideoUrl(null);
    }
  }, [cutoutImage]);

  const selectTheme = (theme: MemorialTheme) => {
    if (theme.requiresGeneration) {
      onSelectCustomBackground?.(theme);
      return;
    }
    try {
      localStorage.setItem("eternal_beam_theme_key", theme.themeKey);
      localStorage.setItem("eternal_beam_theme_id", String(theme.id));
    } catch {
      /* ignore */
    }
    onSelectTheme(theme.id);
  };

  return (
    <div className="theme-selection-screen flex h-full min-h-0 flex-col overflow-hidden bg-[#0a0a0a]">
      <header className="shrink-0 px-5 pt-[max(2.75rem,env(safe-area-inset-top,0px))] pb-3 flex items-center relative z-10">
        <button
          type="button"
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center bg-white/10"
          aria-label={memorialT(language).common.back}
        >
          <ArrowLeft className="w-4 h-4 text-[#E2E2E2]" strokeWidth={1.5} />
        </button>
        <h1 className="flex-1 text-center text-lg font-medium text-[#F1E5D1]">{tc.title}</h1>
        <div className="w-10" />
      </header>

      <div className="theme-selection-screen__scroll hide-scrollbar min-h-0 flex-1 overflow-y-auto">
        {!cutoutImage ? (
          <div className="mx-5 mb-2 px-4 py-2.5 rounded-xl text-[13px] bg-amber-900/25 text-[#e8c97a] border border-amber-600/35">
            {tc.cutoutMissing}
          </div>
        ) : null}

        {/* Neutral preview — no theme background yet */}
        <div className="px-5 py-2">
          <div className="theme-selection-screen__preview relative aspect-[4/3] mx-auto rounded-2xl overflow-hidden border border-white/10 bg-[#0a0a0c]">
            <CutoutStage className="absolute inset-0">
              {idleVideoUrl ? (
                <IdleLoopVideo
                  src={idleVideoUrl}
                  className="cutout-stage__subject"
                />
              ) : cutoutImage ? (
                <img
                  src={cutoutImage}
                  alt=""
                  className="cutout-stage__subject"
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center">
                  <p className="text-sm text-[#888]">{tc.subject}</p>
                </div>
              )}
            </CutoutStage>
          </div>
          <p className="mt-2 text-center text-[10px] text-[#666]">{tc.previewNeutralHint}</p>
        </div>

        {/* Free themes */}
        <div className="px-5 pb-3">
          <p className="text-[11px] uppercase tracking-widest text-[#a8e6a3] mb-1 px-1">{tc.freeSection}</p>
          <p className="text-[10px] text-[#666] mb-2 px-1">{tc.freeSectionHint}</p>
          <ThemeCarousel
            themes={freeMemorialThemes}
            selectedTheme={selectedTheme}
            themeLabel={themeLabel}
            carouselRef={freeCarouselRef}
            onSelect={selectTheme}
          />
        </div>

        {/* Premium themes */}
        <div className="px-5 pb-2">
          <p className="text-[11px] uppercase tracking-widest text-[#f5d77a] mb-1 px-1">{tc.premiumSection}</p>
          <p className="text-[10px] text-[#666] mb-2 px-1">{tc.premiumSectionHint}</p>
          <ThemeCarousel
            themes={premiumMemorialThemes}
            selectedTheme={selectedTheme}
            themeLabel={themeLabel}
            carouselRef={premiumCarouselRef}
            onSelect={selectTheme}
          />
          <p className="mt-2 text-[10px] text-[#888]">{tc.swipeHint}</p>
        </div>
      </div>

      <div className="theme-selection-footer shrink-0 px-5 pt-3 space-y-2 relative z-20">
        <button
          type="button"
          onClick={onContinue}
          disabled={!selectedTheme}
          className="cta-gold w-full py-3.5 rounded-2xl font-medium text-[15px] disabled:opacity-45 disabled:cursor-not-allowed"
        >
          {selectedTheme
            ? isPremiumTheme(selectedTheme)
              ? tc.continuePremium
              : tc.continueFree
            : tc.selectFirst}
        </button>
        <button type="button" onClick={onSkip} className="w-full py-2.5 text-sm text-[#888]">
          {tc.skip}
        </button>
      </div>
    </div>
  );
}
