"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Flower2 } from "lucide-react";
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
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  language?: string;
  onSelectTheme: (themeId: number) => void;
  /** requiresGeneration 테마(예: custom_photo_bg) 카드를 탭했을 때 */
  onSelectCustomBackground?: (theme: MemorialTheme) => void;
  onContinue: (themeId: number) => void;
  onSkip: () => void;
  onBack: () => void;
}

function findCenteredThemeId(
  container: HTMLDivElement,
  themes: MemorialTheme[]
): number | null {
  const center = container.scrollLeft + container.clientWidth / 2;
  let bestId: number | null = null;
  let bestDist = Infinity;

  for (let i = 0; i < container.children.length; i++) {
    const card = container.children[i] as HTMLElement;
    const cardCenter = card.offsetLeft + card.offsetWidth / 2;
    const dist = Math.abs(center - cardCenter);
    if (dist < bestDist) {
      bestDist = dist;
      bestId = themes[i]?.id ?? null;
    }
  }
  return bestId;
}

const ThemeThumb = memo(function ThemeThumb({
  theme,
  loadImage,
}: {
  theme: MemorialTheme;
  loadImage: boolean;
}) {
  if (!loadImage) {
    return <div className="absolute inset-0 bg-[#141416]" aria-hidden />;
  }
  return (
    <img
      src={theme.thumb}
      alt=""
      loading="lazy"
      decoding="async"
      className="absolute inset-0 h-full w-full object-cover"
    />
  );
});

const ThemeCarousel = memo(function ThemeCarousel({
  themes,
  selectedTheme,
  themeLabel,
  carouselRef,
  carouselId,
  isInteractionTarget,
  onInteractionStart,
  onSelect,
  onSnapTheme,
}: {
  themes: MemorialTheme[];
  selectedTheme: number | null;
  themeLabel: (th: MemorialTheme) => string;
  carouselRef: React.RefObject<HTMLDivElement | null>;
  carouselId: "free" | "premium";
  isInteractionTarget: () => boolean;
  onInteractionStart: (id: "free" | "premium") => void;
  onSelect: (theme: MemorialTheme) => void;
  onSnapTheme: (themeId: number, source: "free" | "premium") => void;
}) {
  const [focusIndex, setFocusIndex] = useState(() =>
    Math.max(0, themes.findIndex((t) => t.id === selectedTheme))
  );

  useEffect(() => {
    const idx = themes.findIndex((t) => t.id === selectedTheme);
    if (idx >= 0) setFocusIndex(idx);
  }, [selectedTheme, themes]);

  useEffect(() => {
    const el = carouselRef.current;
    if (!el) return;

    let raf = 0;
    const syncFromScroll = () => {
      if (!isInteractionTarget()) return;
      const id = findCenteredThemeId(el, themes);
      if (id == null) return;
      const idx = themes.findIndex((t) => t.id === id);
      if (idx >= 0) setFocusIndex(idx);
      onSnapTheme(id, carouselId);
    };

    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(syncFromScroll);
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(raf);
      el.removeEventListener("scroll", onScroll);
    };
  }, [carouselId, carouselRef, themes, onSnapTheme, isInteractionTarget]);

  const scrollByPage = useCallback(
    (dir: -1 | 1) => {
      const el = carouselRef.current;
      if (!el) return;
      el.scrollBy({ left: dir * Math.max(140, el.clientWidth * 0.65), behavior: "smooth" });
    },
    [carouselRef]
  );

  return (
    <div className="relative">
      <button
        type="button"
        aria-label="Previous"
        onClick={() => scrollByPage(-1)}
        className="absolute left-0 top-1/2 z-10 -translate-y-1/2 rounded-full p-2 bg-white/15 border border-white/25"
      >
        <ArrowLeft className="h-4 w-4 text-white" />
      </button>
      <button
        type="button"
        aria-label="Next"
        onClick={() => scrollByPage(1)}
        className="absolute right-0 top-1/2 z-10 -translate-y-1/2 rounded-full p-2 bg-white/15 border border-white/25"
      >
        <ArrowRight className="h-4 w-4 text-white" />
      </button>

      <div
        ref={carouselRef}
        onPointerDown={() => onInteractionStart(carouselId)}
        className="hide-scrollbar flex snap-x snap-mandatory gap-3 overflow-x-auto px-10 pb-2"
      >
        {themes.map((theme, index) => {
          const selected = selectedTheme === theme.id;
          const loadImage = Math.abs(index - focusIndex) <= 1;
          return (
            <button
              key={theme.id}
              type="button"
              data-theme-id={theme.id}
              onClick={() => onSelect(theme)}
              className={`theme-selection-screen__carousel-card relative aspect-[3/4] w-[38%] shrink-0 snap-center rounded-2xl overflow-hidden border-2 transition-[border-color,box-shadow] duration-150 ${
                selected ? "border-[#c9a227] shadow-[0_0_0_1px_rgba(201,162,39,0.35)]" : "border-transparent"
              }`}
            >
              <ThemeThumb theme={theme} loadImage={loadImage} />
              <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-40 pointer-events-none`} />
              {theme.requiresGeneration ? (
                <div className="absolute top-2 left-2 w-5 h-5 rounded-full bg-white/15 border border-white/30 flex items-center justify-center">
                  <Flower2 className="w-3 h-3 text-[#f5d77a]" strokeWidth={2} />
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
});

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
  const interactionCarouselRef = useRef<"free" | "premium" | null>(null);
  const [pipelineCutout, setPipelineCutout] = useState<string | null>(null);
  const [idleVideoUrl, setIdleVideoUrl] = useState<string>("");
  const [highlightTheme, setHighlightTheme] = useState<number | null>(selectedTheme);

  const isInteractionTarget = useCallback(
    (id: "free" | "premium") => () => interactionCarouselRef.current === id,
    []
  );

  const markInteraction = useCallback((id: "free" | "premium") => {
    interactionCarouselRef.current = id;
  }, []);

  useEffect(() => {
    setHighlightTheme(selectedTheme);
  }, [selectedTheme]);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (!raw) {
        setPipelineCutout(null);
        setIdleVideoUrl("");
        return;
      }
      const pipeline = JSON.parse(raw) as StoredPipeline;
      const cutout =
        cutoutImage ||
        pipeline.cutout_display_url ||
        pipeline.dog_only_nobg_url ||
        null;
      setPipelineCutout(cutout);
      setIdleVideoUrl(pipeline.idle_video_url || "");
    } catch {
      setPipelineCutout(null);
      setIdleVideoUrl("");
    }
  }, [cutoutImage]);

  const selectTheme = useCallback(
    (theme: MemorialTheme, source: "free" | "premium") => {
      interactionCarouselRef.current = source;
      setHighlightTheme(theme.id);
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
    },
    [onSelectCustomBackground, onSelectTheme]
  );

  const snapSelectTheme = useCallback(
    (themeId: number, source: "free" | "premium") => {
      if (interactionCarouselRef.current !== source) return;
      setHighlightTheme(themeId);
      const theme = (source === "free" ? freeMemorialThemes : premiumMemorialThemes).find(
        (t) => t.id === themeId
      );
      if (!theme || theme.requiresGeneration) return;
      try {
        localStorage.setItem("eternal_beam_theme_key", theme.themeKey);
        localStorage.setItem("eternal_beam_theme_id", String(theme.id));
      } catch {
        /* ignore */
      }
      onSelectTheme(themeId);
    },
    [onSelectTheme]
  );

  const activeTheme = highlightTheme ?? selectedTheme;

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

        <div className="px-5 py-2">
          <div className="theme-selection-screen__preview relative aspect-[4/3] mx-auto rounded-2xl overflow-hidden border border-white/10 bg-[#0a0a0c]">
            <CutoutStage className="absolute inset-0">
              <PetIdleDisplay
                idleVideoUrl={idleVideoUrl}
                cutoutUrl={cutoutImage || pipelineCutout}
                className="cutout-stage__subject"
              />
            </CutoutStage>
          </div>
          <p className="mt-2 text-center text-[10px] text-[#666]">{tc.previewNeutralHint}</p>
        </div>

        <div className="px-5 pb-3">
          <p className="text-[11px] uppercase tracking-widest text-[#a8e6a3] mb-1 px-1">{tc.freeSection}</p>
          <p className="text-[10px] text-[#666] mb-2 px-1">{tc.freeSectionHint}</p>
          <ThemeCarousel
            themes={freeMemorialThemes}
            selectedTheme={activeTheme}
            themeLabel={themeLabel}
            carouselRef={freeCarouselRef}
            carouselId="free"
            isInteractionTarget={isInteractionTarget("free")}
            onInteractionStart={markInteraction}
            onSelect={(theme) => selectTheme(theme, "free")}
            onSnapTheme={snapSelectTheme}
          />
        </div>

        <div className="px-5 pb-2">
          <p className="text-[11px] uppercase tracking-widest text-[#f5d77a] mb-1 px-1">{tc.premiumSection}</p>
          <p className="text-[10px] text-[#666] mb-2 px-1">{tc.premiumSectionHint}</p>
          <ThemeCarousel
            themes={premiumMemorialThemes}
            selectedTheme={activeTheme}
            themeLabel={themeLabel}
            carouselRef={premiumCarouselRef}
            carouselId="premium"
            isInteractionTarget={isInteractionTarget("premium")}
            onInteractionStart={markInteraction}
            onSelect={(theme) => selectTheme(theme, "premium")}
            onSnapTheme={snapSelectTheme}
          />
          <p className="mt-2 text-[10px] text-[#888]">{tc.swipeHint}</p>
        </div>
      </div>

      <div className="theme-selection-footer shrink-0 px-5 pt-3 space-y-2 relative z-20">
        <button
          type="button"
          onClick={() => activeTheme && onContinue(activeTheme)}
          disabled={!activeTheme}
          className="cta-gold w-full py-3.5 rounded-2xl font-medium text-[15px] disabled:opacity-45 disabled:cursor-not-allowed"
        >
          {activeTheme
            ? isPremiumTheme(activeTheme)
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
