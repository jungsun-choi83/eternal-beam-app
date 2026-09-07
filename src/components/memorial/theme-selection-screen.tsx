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
  getMemorialTheme,
  ORIGINAL_PHOTO_THEME_KEY,
  premiumMemorialThemes,
  type MemorialTheme,
} from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import { resetThemeBackgroundSyncCache } from "@/lib/device-theme-sync";
import { useThemeOwnership } from "@/components/memorial/use-theme-ownership";
import { formatCredits, themeRow, type ThemeOffer } from "@/lib/theme-ownership";
import { CreditPackSheet } from "@/components/memorial/credit-pack-sheet";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  /**
   * "원본 사진 그대로" 에 쓸 **해결된 한 장.** 부모가 한 번 정해 내려 준다.
   *
   * 이 화면이 직접 localStorage 를 읽지 않는 이유: 방금 올린 사진은 React
   * 상태에 있고 저장은 그보다 늦거나 실패할 수 있다. 각자 읽으면 카드와 큰
   * 미리보기와 생성이 서로 다른 그림을 본다.
   */
  originalPhoto?: string | null;
  selectedTheme: number | null;
  language?: string;
  onSelectTheme: (themeId: number) => void;
  /** requiresGeneration 테마(예: custom_photo_bg) 카드를 탭했을 때 */
  onSelectCustomBackground?: (theme: MemorialTheme) => void;
  onContinue: (themeId: number) => void;
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
  originalPhoto,
}: {
  theme: MemorialTheme;
  loadImage: boolean;
  /** 원본 갈래 카드가 보여 줄 사진. 큰 미리보기·생성과 **같은 값**이다. */
  originalPhoto?: string | null;
}) {
  if (!loadImage) {
    return <div className="absolute inset-0 bg-[#141416]" aria-hidden />;
  }
  // "원본 사진 그대로"의 썸네일은 **고객이 올린 사진 자체**다. 고정 에셋을 두면
  // 다른 사진처럼 보이고, 고객은 자기 배경이 어떤 것인지 확인할 수 없다.
  const src =
    theme.themeKey === ORIGINAL_PHOTO_THEME_KEY
      ? originalPhoto || theme.thumb
      : theme.thumb;
  if (!src) {
    return <div className="absolute inset-0 bg-[#141416]" aria-hidden />;
  }
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      className="absolute inset-0 h-full w-full object-cover"
    />
  );
});

/**
 * 소유 상태 배지 — FREE / OWNED / 가격 / 준비 중.
 *
 * themes.ts 의 하드코딩된 `price`("$2.99")를 더 이상 쓰지 않는다. 그 값은 레거시
 * PayPal 표시용이고, 실제 판매 여부·가격은 **서버 카탈로그**가 정한다.
 * 두 곳이 다르면 눌러도 거절당하는 버튼이 생긴다.
 */
function ThemeOwnershipBadge({
  theme,
  offers,
}: {
  theme: MemorialTheme;
  offers: Map<string, ThemeOffer>;
}) {
  const row = themeRow(theme, offers);
  // 배지도 **크레딧**을 보여 준다 — CTA 와 다른 통화를 쓰면 무엇을 내는지 헷갈린다.
  const price = formatCredits(row.creditPrice);

  const [label, color] =
    row.state === "free"
      ? ["FREE", "#a8e6a3"]
      : row.state === "owned"
        ? ["OWNED", "#a8e6a3"]
        : row.state === "not-owned"
          ? [price ? `${price} 크레딧` : "BUY", "#f5d77a"]
          : ["준비 중", "#9a9a9a"];

  return (
    <div className="absolute top-2 right-2 rounded-full bg-black/50 px-1.5 py-0.5">
      <span className="text-[8px] tracking-wide" style={{ color }}>
        {label}
      </span>
    </div>
  );
}

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
  offers,
  originalPhoto = null,
}: {
  themes: MemorialTheme[];
  selectedTheme: number | null;
  /** 서버 카탈로그. 비어 있으면 폴백 표시(유료는 잠김). */
  offers: Map<string, ThemeOffer>;
  themeLabel: (th: MemorialTheme) => string;
  carouselRef: React.RefObject<HTMLDivElement | null>;
  carouselId: "free" | "premium";
  isInteractionTarget: () => boolean;
  onInteractionStart: (id: "free" | "premium") => void;
  onSelect: (theme: MemorialTheme) => void;
  onSnapTheme: (themeId: number, source: "free" | "premium") => void;
  originalPhoto?: string | null;
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
          // ±1 가상화는 그대로 두되, **선택된 카드는 거리와 무관하게 로드한다.**
          // 멀리 있는 테마를 눌렀을 때 그 카드가 검은 채로 남으면, 고객은
          // 자기가 무엇을 골랐는지 볼 수 없다.
          const loadImage = Math.abs(index - focusIndex) <= 1 || selected;
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
              <ThemeThumb theme={theme} loadImage={loadImage} originalPhoto={originalPhoto} />
              <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-40 pointer-events-none`} />
              {theme.requiresGeneration ? (
                <div className={`absolute left-2 w-5 h-5 rounded-full bg-white/15 border border-white/30 flex items-center justify-center ${selected ? "top-9" : "top-2"}`}>
                  <Flower2 className="w-3 h-3 text-[#f5d77a]" strokeWidth={2} />
                </div>
              ) : null}
              {selected ? (
                <div className="absolute top-2 left-2 w-5 h-5 rounded-full bg-[#c9a227] flex items-center justify-center">
                  <Check className="w-3 h-3 text-[#0a0a0a]" strokeWidth={3} />
                </div>
              ) : null}
              <ThemeOwnershipBadge theme={theme} offers={offers} />
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
  originalPhoto = null,
  selectedTheme,
  language = "ko",
  onSelectTheme,
  onSelectCustomBackground,
  onContinue,
  onBack,
}: ThemeSelectionScreenProps) {
  const tc = memorialT(language).theme;
  const themeLabel = (th: MemorialTheme) =>
    themeDisplayName(language === "ko" ? "ko" : "en", th);
  const freeCarouselRef = useRef<HTMLDivElement | null>(null);
  const ownership = useThemeOwnership();
  const premiumCarouselRef = useRef<HTMLDivElement | null>(null);
  const interactionCarouselRef = useRef<"free" | "premium" | null>(null);
  const [pipelineCutout, setPipelineCutout] = useState<string | null>(null);
  const [idleVideoUrl, setIdleVideoUrl] = useState<string>("");
  /** 이 화면이 트는 영상이 배경을 이미 담고 있는가 (Phase 25).
   *  아래 useEffect 가 **이미 파싱하고 있던** 객체에서 그대로 꺼낸다. */
  const [idleBaked, setIdleBaked] = useState(false);
  const [highlightTheme, setHighlightTheme] = useState<number | null>(selectedTheme);
  /** 크레딧이 모자랄 때 여는 팩 시트. 사용자가 눌러야만 열린다. */
  const [showPacks, setShowPacks] = useState(false);

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
    resetThemeBackgroundSyncCache();
  }, []);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (!raw) {
        setPipelineCutout(null);
        setIdleVideoUrl("");
        setIdleBaked(false);
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
      // 영상이 있을 때만 의미가 있다 — 없으면 나가는 것은 정적 누끼다.
      setIdleBaked(
        Boolean(pipeline.idle_video_url) && pipeline.background_baked === true
      );
    } catch {
      setPipelineCutout(null);
      setIdleVideoUrl("");
      setIdleBaked(false);
    }
  }, [cutoutImage]);

  const selectTheme = useCallback(
    (theme: MemorialTheme, source: "free" | "premium") => {
      interactionCarouselRef.current = source;
      setHighlightTheme(theme.id);
    },
    []
  );

  const snapSelectTheme = useCallback(
    (themeId: number, source: "free" | "premium") => {
      if (interactionCarouselRef.current !== source) return;
      setHighlightTheme(themeId);
      const theme = (source === "free" ? freeMemorialThemes : premiumMemorialThemes).find(
        (t) => t.id === themeId
      );
      if (!theme) return;
    },
    []
  );

  const activeTheme = highlightTheme ?? selectedTheme;
  const previewTheme = activeTheme != null ? getMemorialTheme(activeTheme) : null;
  const previewIsOriginal = previewTheme?.themeKey === ORIGINAL_PHOTO_THEME_KEY;
  const previewBgVideo = getEffectiveBgVideo(previewTheme);
  /**
   * 원본을 골랐는데 보여 줄 사진이 없다.
   *
   * 검은 판을 보여 주고 넘어가게 두지 않는다 — 그 검은 판이 그대로 유료 생성에
   * 들어가고, 고객은 결제 뒤에야 알게 된다.
   */
  const originalMissing = Boolean(previewIsOriginal && !originalPhoto);
  const activeRow = previewTheme ? themeRow(previewTheme, ownership.offers) : null;
  const activePrice = formatCredits(activeRow?.creditPrice ?? null);
  /**
   * 지금 이 테마를 사기에 **몇 크레딧이 모자라는가.** 살 수 있으면 null.
   *
   * 잔액을 못 받았으면(null) 판정하지 않는다 — 모르는 것을 "부족"으로 읽으면
   * 살 수 있는 사용자에게 충전을 강요하게 된다.
   */
  const shortfall =
    activeRow?.action === "buy" &&
    activeRow.creditPrice != null &&
    ownership.balance != null &&
    activeRow.creditPrice > ownership.balance
      ? activeRow.creditPrice - ownership.balance
      : null;
  const needsCustomBackground = Boolean(
    previewTheme?.requiresGeneration && !previewBgVideo
  );
  const waitingForCatalog = Boolean(
    previewTheme?.premium && ownership.loading && activeRow?.state === "unknown"
  );
  const primaryDisabled =
    !previewTheme ||
    originalMissing ||
    waitingForCatalog ||
    ownership.buying != null ||
    activeRow?.action === "none";

  const commitTheme = useCallback(
    (theme: MemorialTheme) => {
      try {
        localStorage.setItem("eternal_beam_theme_key", theme.themeKey);
        localStorage.setItem("eternal_beam_theme_id", String(theme.id));
      } catch {
        /* ignore */
      }
      onSelectTheme(theme.id);
    },
    [onSelectTheme]
  );

  const handlePrimaryAction = useCallback(async () => {
    if (!previewTheme || originalMissing || !activeRow) return;

    if (activeRow.action === "buy") {
      // 크레딧이 모자라면 **구매를 시도하지 않는다.** 402 를 받아 오류 문구를
      // 띄우는 대신, 부족하다는 사실을 미리 알고 충전으로 안내한다 — 사용자가
      // 실패를 겪을 이유가 없다.
      if (shortfall != null) {
        setShowPacks(true);
        return;
      }
      await ownership.buy(previewTheme.themeKey);
      return;
    }
    if (!activeRow.usable) return;

    commitTheme(previewTheme);
    if (needsCustomBackground) {
      onSelectCustomBackground?.(previewTheme);
      return;
    }
    onContinue(previewTheme.id);
  }, [
    activeRow,
    commitTheme,
    needsCustomBackground,
    onContinue,
    onSelectCustomBackground,
    originalMissing,
    ownership,
    previewTheme,
    shortfall,
  ]);
  // 커스텀 배경은 getEffectiveBgVideo 가 저장된 사용자 배경을 돌려준다. 아직
  // 만들지 않았다면 카드 아트(플레이스홀더)가 나오고, 실제 거절은 다음 화면의
  // 장면 준비에서 일어난다.

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
            {/* ── 고른 배경을 **즉시** 보여 준다 ─────────────────────────────
                예전에는 이 자리가 누끼만 그렸다("배경은 다음 단계에서"). 그래서
                테마를 눌러도 화면이 바뀌지 않았고, 고객은 무엇을 고르는지 모르는
                채 다음으로 넘어갔다. 다음 화면의 합성 규칙을 그대로 쓴다 —
                여기서 본 그림과 미리보기에서 볼 그림이 같아야 한다. */}
            {originalMissing ? (
              <div
                role="alert"
                className="absolute inset-0 flex items-center justify-center px-6 text-center text-[13px] bg-amber-900/30 text-[#e8c97a]"
              >
                {tc.originalMissing}
              </div>
            ) : (
              <>
                {previewIsOriginal ? (
                  // 원본 갈래에는 펫을 **얹지 않는다** — 사진에 이미 아이가 있다.
                  <img
                    src={originalPhoto ?? undefined}
                    alt=""
                    className="absolute inset-0 h-full w-full object-cover"
                  />
                ) : (
                  <>
                    {previewBgVideo ? (
                      <ThemeBackgroundVideo
                        key={`theme-sel-bg-${activeTheme}-${previewBgVideo}`}
                        src={previewBgVideo}
                        poster={previewTheme?.thumb}
                      />
                    ) : previewTheme?.thumb ? (
                      <div
                        className="absolute inset-0 bg-center bg-cover"
                        style={{ backgroundImage: `url(${previewTheme.thumb})` }}
                      />
                    ) : null}
                    {previewTheme ? (
                      <div
                        className={`absolute inset-0 bg-gradient-to-b ${previewTheme.gradient} opacity-25`}
                      />
                    ) : null}
                    <CutoutStage plain className="absolute inset-0">
                      <PetIdleDisplay
                        idleVideoUrl={idleVideoUrl}
                        cutoutUrl={cutoutImage || pipelineCutout}
                        // 테마 선택은 확인 전 단계 — 데모 mp4 폴백 없이 정적 누끼로 보여준다.
                        allowDemoFallback={false}
                        // 저장된 파이프라인에 이미 들어 있던 값이다 (Phase 25).
                        backgroundBaked={idleBaked}
                        className="cutout-stage__subject"
                      />
                    </CutoutStage>
                  </>
                )}
              </>
            )}
          </div>
          <p className="mt-2 text-center text-[10px] text-[#666]">
            {previewIsOriginal ? tc.previewOriginalHint : tc.previewNeutralHint}
          </p>
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
            offers={ownership.offers}
            onSelect={(theme) => selectTheme(theme, "free")}
            onSnapTheme={snapSelectTheme}
            originalPhoto={originalPhoto}
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
            offers={ownership.offers}
            onSelect={(theme) => selectTheme(theme, "premium")}
            onSnapTheme={snapSelectTheme}
            originalPhoto={originalPhoto}
          />
          <p className="mt-2 text-[10px] text-[#888]">{tc.swipeHint}</p>
        </div>
      </div>

      <div className="theme-selection-footer shrink-0 px-5 pt-3 space-y-2 relative z-20">
        {ownership.error ? (
          <p role="alert" className="text-center text-xs text-red-300">
            {ownership.error === "UNAUTHENTICATED"
              ? tc.signInToBuy
              : ownership.error === "INSUFFICIENT_CREDITS"
                ? tc.insufficientCredits
                : tc.purchaseFailed}
          </p>
        ) : null}
        {/* 잔액 · 가격을 CTA 바로 위에 둔다 — "잔액 12 / 가격 5" 를 보고 누르는
            것이 이 화면의 결정이다. 살 수 있는 테마일 때만 보여 준다. */}
        {ownership.balance != null && activeRow?.action === "buy" && activePrice ? (
          <p className="text-center text-[11px] text-[#9a9a9a]">
            {tc.balanceLabel(ownership.balance)}
            {shortfall != null ? (
              <span className="ml-2 text-[#f5d77a]">{tc.needMoreCredits(shortfall)}</span>
            ) : null}
          </p>
        ) : null}
        <button
          type="button"
          onClick={() => void handlePrimaryAction()}
          disabled={primaryDisabled}
          className="cta-gold w-full py-3.5 rounded-2xl font-medium text-[15px] disabled:opacity-45 disabled:cursor-not-allowed"
        >
          {originalMissing
            ? tc.originalMissingCta
            : !previewTheme
              ? tc.selectFirst
              : ownership.buying === previewTheme.themeKey
                ? tc.buying
                : waitingForCatalog
                  ? tc.loadingPrice
                  : activeRow?.action === "buy"
                    ? shortfall != null
                      ? tc.getCreditsCta
                      : tc.buyFor(activePrice ?? "")
                    : activeRow?.action === "none"
                      ? tc.comingSoon
                      : needsCustomBackground
                        ? tc.createCustomBackground
                        : tc.continueFree}
        </button>
      </div>

      {showPacks && previewTheme ? (
        <CreditPackSheet
          balance={ownership.balance}
          needed={activeRow?.creditPrice ?? null}
          // 충전 뒤 **이 배경으로** 돌아온다. 홈이 아니다.
          returnThemeKey={previewTheme.themeKey}
          onClose={() => setShowPacks(false)}
        />
      ) : null}
    </div>
  );
}
