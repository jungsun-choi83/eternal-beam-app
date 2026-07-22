"use client";

import { useRef } from "react";
import { ArrowLeft, ArrowRight, Check, Sparkles } from "lucide-react";
import { memorialT, themeDisplayName } from "@/components/memorial/memorial-i18n";
import { memorialThemes, type MemorialTheme } from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  language?: string;
  onSelectTheme: (themeId: number) => void;
  /** requiresGeneration 테마(예: custom_photo_bg) 카드를 탭했을 때 — 일반 선택과
   * 달리 바로 생성 화면으로 이동해야 하므로 별도 콜백으로 분리. */
  onSelectCustomBackground?: (theme: MemorialTheme) => void;
  onContinue: () => void;
  onSkip: () => void;
  onBack: () => void;
}

const themes = memorialThemes;

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
  const currentTheme = themes.find((t) => t.id === selectedTheme);
  const themeLabel = (th: MemorialTheme) =>
    themeDisplayName(language === "ko" ? "ko" : "en", th);
  const carouselRef = useRef<HTMLDivElement | null>(null);

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

  const scrollCarousel = (direction: -1 | 1) => {
    const el = carouselRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * Math.max(140, el.clientWidth * 0.65), behavior: "smooth" });
  };

  return (
    <div className="theme-selection-screen flex h-full min-h-0 flex-col overflow-hidden">
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

        <div className="px-5 py-3">
          <div className="theme-selection-screen__preview theme-preview-frame relative aspect-[4/3] mx-auto">
            {currentTheme && getEffectiveBgVideo(currentTheme) ? (
              <ThemeBackgroundVideo
                src={getEffectiveBgVideo(currentTheme)!}
                poster={currentTheme.thumb}
              />
            ) : currentTheme ? (
              <div
                className="absolute inset-0 bg-cover bg-center"
                style={{ backgroundImage: `url(${currentTheme.thumb})` }}
              />
            ) : null}
            {currentTheme ? (
              <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient} opacity-30`} />
            ) : null}

            <div className="theme-preview-frame__subject">
              {cutoutImage ? (
                <img
                  src={cutoutImage}
                  alt=""
                  style={{
                    filter: currentTheme
                      ? `drop-shadow(0 12px 28px ${currentTheme.accent}aa)`
                      : "drop-shadow(0 12px 28px rgba(201,162,39,0.5))",
                  }}
                />
              ) : (
                <p className="text-sm text-[#E2E2E2]">{tc.subject}</p>
              )}
            </div>
          </div>
        </div>

        <div className="px-5 pb-3">
          <p className="text-[11px] uppercase tracking-widest text-[#A1A1A6] mb-3 px-1">{tc.subtitle}</p>

          <div className="relative">
            <button
              type="button"
              aria-label={tc.prevTheme}
              onClick={() => scrollCarousel(-1)}
              className="absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-full p-2 bg-white/15 border border-white/25"
            >
              <ArrowLeft className="h-4 w-4 text-white" />
            </button>
            <button
              type="button"
              aria-label={tc.nextTheme}
              onClick={() => scrollCarousel(1)}
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
                    onClick={() => selectTheme(theme)}
                    className={`theme-selection-screen__carousel-card relative aspect-[3/4] shrink-0 snap-center rounded-2xl overflow-hidden border-2 transition-colors ${
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
                    ) : null}
                    <div className="absolute bottom-2 left-0 right-0 text-center px-1">
                      <span className="text-[10px] text-[#F1E5D1] tracking-wide">{themeLabel(theme)}</span>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>
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
          {selectedTheme ? tc.continue : tc.selectFirst}
        </button>
        <button type="button" onClick={onSkip} className="w-full py-2.5 text-sm text-[#888]">
          {tc.skip}
        </button>
      </div>
    </div>
  );
}
