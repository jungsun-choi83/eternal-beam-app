"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Check, Flower2, Lock } from "lucide-react";
import { BackButton } from "@/components/ui/screen-header";
import { memorialT, themeDisplayName } from "@/components/memorial/memorial-i18n";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import {
  freeMemorialThemes,
  getMemorialTheme,
  getThemeCardThumb,
  ORIGINAL_PHOTO_THEME_KEY,
  premiumMemorialThemes,
  type MemorialTheme,
} from "@/components/memorial/themes";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import { resetThemeBackgroundSyncCache } from "@/lib/device-theme-sync";
import { useThemeOwnership } from "@/components/memorial/use-theme-ownership";
import { formatCredits, themeRow, type ThemeOffer } from "@/lib/theme-ownership";
import { groupPremiumThemes } from "@/lib/theme-groups";
import { computeCreateFlowSteps } from "@/lib/create-flow-steps";
import { CreateFlowStepper, type CreateFlowStepView } from "@/components/memorial/create-flow-stepper";
import { CreditPackSheet } from "@/components/memorial/credit-pack-sheet";
import { PetPhoto } from "@/components/memorial/pet-photo";
import { applyLibraryOverride, type LibraryPublication } from "@/lib/library-publication";

interface ThemeSelectionScreenProps {
  cutoutImage: string | null;
  /**
   * My Library 경로인가 — 부모(MyLibraryScreen)가 동기적으로 내려주는 값.
   * sessionStorage 를 읽는 effect 가 돌 때까지 기다리지 않고 **첫 렌더부터**
   * 이 값으로 라이브러리 모드를 확정한다.
   */
  isLibraryFlow?: boolean;
  /** isLibraryFlow 일 때만 의미 있다 — 발행된 모션의 정본 메타데이터. */
  libraryPublication?: LibraryPublication | null;
  /**
   * "원본 사진 그대로" 에 쓸 **해결된 한 장.** 부모가 한 번 정해 내려 준다.
   *
   * 이 화면이 직접 localStorage 를 읽지 않는 이유: 방금 올린 사진은 React
   * 상태에 있고 저장은 그보다 늦거나 실패할 수 있다. 각자 읽으면 카드와 생성이
   * 서로 다른 그림을 본다.
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

/** 카드 그리드/필터 알약이 쓰는 상태 — Included/Owned/Premium/Custom. */
type ThemeCardGroup = "included" | "owned" | "premium" | "custom";
type ThemeFilter = "all" | ThemeCardGroup;

interface ThemeCard {
  theme: MemorialTheme;
  group: ThemeCardGroup;
}

const FILTER_ORDER: ThemeFilter[] = ["all", "included", "owned", "premium", "custom"];

/**
 * 카드 썸네일 = 테마 gradient 바탕 **위에** 카탈로그의 실제 미리보기 사진.
 *
 * 바탕을 먼저 까는 이유: theme-thumbs 의 원본은 수 MB 짜리 풀사이즈 사진이라
 * (golden_meadow 9MB, sunset 7MB …) 느린 회선에서는 몇 초 동안 사진이 오지
 * 않는다. 캐러셀 시절에는 ±1 카드만 로드해 이 지연이 가려졌지만, 그리드는
 * 카드 11장을 한 번에 그린다 — 바탕이 없으면 그동안 카드가 "이름만 있는
 * 빈 상자"로 보인다. gradient 는 테마마다 카탈로그가 이미 갖고 있는 값이라
 * 새 에셋을 만들지 않고, 사진이 도착하면 그 위를 덮는다.
 *
 * 사진 주소는 getThemeCardThumb 가 정한다 — "원본 사진 그대로"는 고객이 올린
 * 사진 자체(없으면 빈 바탕), 나머지는 카탈로그 thumb. 실제 에셋이 없는 경우
 * (원본 미주입, 404) 에만 바탕이 그대로 남는다. alt="" 라 깨진 이미지 아이콘도
 * 그려지지 않는다.
 */
const ThemeThumb = ({
  theme,
  originalPhoto,
}: {
  theme: MemorialTheme;
  /** 원본 갈래 카드가 보여 줄 사진. 생성과 **같은 값**이다. */
  originalPhoto?: string | null;
}) => {
  const src = getThemeCardThumb(theme, originalPhoto);
  // 원본 갈래 카드만 **사용자 사진**이다 — 전체가 보이게(contain) 그린다.
  // 카탈로그 썸네일은 브랜드 사진이라 그대로 cover 크롭을 유지한다.
  const isUserPhoto = theme.themeKey === ORIGINAL_PHOTO_THEME_KEY && Boolean(originalPhoto);
  return (
    <>
      <div className={`theme-grid__thumb-base bg-gradient-to-b ${theme.gradient}`} aria-hidden />
      {src && isUserPhoto ? (
        <PetPhoto src={src} variant="full" className="theme-grid__pet" loading="lazy" />
      ) : src ? (
        <img src={src} alt="" loading="lazy" decoding="async" className="theme-grid__img" />
      ) : null}
    </>
  );
};

/**
 * 카드 아래 상태 문구 — FREE / OWNED / 가격 / 준비 중.
 *
 * themes.ts 의 하드코딩된 `price`("$2.99")를 더 이상 쓰지 않는다. 그 값은 레거시
 * PayPal 표시용이고, 실제 판매 여부·가격은 **서버 카탈로그**가 정한다.
 */
function ThemeStateLabel({
  theme,
  offers,
  tc,
}: {
  theme: MemorialTheme;
  offers: Map<string, ThemeOffer>;
  tc: ReturnType<typeof memorialT>["theme"];
}) {
  const row = themeRow(theme, offers);
  // 가격도 **크레딧**을 보여 준다 — CTA 와 다른 통화를 쓰면 무엇을 내는지 헷갈린다.
  const price = formatCredits(row.creditPrice);

  if (row.state === "free") {
    return <span className="theme-grid__state theme-grid__state--free">{tc.stateFree}</span>;
  }
  if (row.state === "owned") {
    return <span className="theme-grid__state theme-grid__state--owned">{tc.stateOwned}</span>;
  }
  if (row.state === "not-owned" && price) {
    return (
      <span className="theme-grid__state theme-grid__state--premium">
        <Lock className="w-3 h-3" strokeWidth={2} aria-hidden />
        {tc.statePriceCredits(row.creditPrice ?? 0)}
      </span>
    );
  }
  return <span className="theme-grid__state theme-grid__state--coming-soon">{tc.comingSoon}</span>;
}

function ThemeGridCard({
  theme,
  group,
  selected,
  offers,
  themeLabel,
  originalPhoto,
  hasCustomAsset,
  tc,
  onSelect,
}: {
  theme: MemorialTheme;
  group: ThemeCardGroup;
  selected: boolean;
  offers: Map<string, ThemeOffer>;
  themeLabel: (th: MemorialTheme) => string;
  originalPhoto?: string | null;
  /** requiresGeneration 테마가 아니면 항상 true — 자리표시자 갈래가 없다. */
  hasCustomAsset: boolean;
  tc: ReturnType<typeof memorialT>["theme"];
  onSelect: () => void;
}) {
  const isCustom = Boolean(theme.requiresGeneration);
  const isCustomPlaceholder = isCustom && !hasCustomAsset;

  return (
    <button
      type="button"
      data-theme-id={theme.id}
      data-theme-group={group}
      onClick={onSelect}
      aria-pressed={selected}
      className={`theme-grid__card${selected ? " theme-grid__card--selected" : ""}${
        isCustomPlaceholder ? " theme-grid__card--custom" : ""
      }`}
    >
      {/* 모든 카드가 같은 썸네일을 쓴다 — 커스텀도 카탈로그의 자리표시자 에셋을
          그리고, 꽃 배지로 "내 사진으로 만드는 배경"임을 표시한다(캐러셀과 같다). */}
      <div className="theme-grid__thumb">
        <ThemeThumb theme={theme} originalPhoto={originalPhoto} />
        {isCustom ? (
          <div className="theme-grid__badge" aria-hidden>
            <Flower2 className="w-3 h-3" strokeWidth={2} />
          </div>
        ) : null}
        {selected ? (
          <div className="eb-check-mark theme-grid__check">
            <Check className="w-3 h-3" strokeWidth={3} />
          </div>
        ) : null}
      </div>
      <div className="theme-grid__body">
        <p className="theme-grid__name">{isCustomPlaceholder ? tc.customCardTitle : themeLabel(theme)}</p>
        {isCustomPlaceholder ? (
          <p className="theme-grid__state theme-grid__state--free">{tc.customCardHint}</p>
        ) : (
          <ThemeStateLabel theme={theme} offers={offers} tc={tc} />
        )}
      </div>
    </button>
  );
}

export function ThemeSelectionScreen({
  cutoutImage,
  isLibraryFlow = false,
  libraryPublication = null,
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
  const ownership = useThemeOwnership();
  /** My Library 경로로 들어왔는가 — 발행된 영상만 있고 정적 누끼는 없다.
   *  그 경우 "업로드·처리 필요" 경고는 틀린 안내다.
   *  isLibraryFlow prop 으로 **첫 렌더부터** 확정한다 — effect 를 기다리지 않는다. */
  const [isLibrarySource, setIsLibrarySource] = useState(isLibraryFlow);
  const [highlightTheme, setHighlightTheme] = useState<number | null>(selectedTheme);
  const [filter, setFilter] = useState<ThemeFilter>("all");
  /** 크레딧이 모자랄 때 여는 팩 시트. 사용자가 눌러야만 열린다. */
  const [showPacks, setShowPacks] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const footerRef = useRef<HTMLDivElement>(null);

  /**
   * 하단 CTA 는 스크롤 영역의 형제라 화면 아래에 고정된다. 그 높이는 구매 오류나
   * 잔액 안내가 나타날 때 달라질 수 있으므로 상수로 추측하지 않고 실제 border-box
   * 높이(이미 safe-area padding 포함)를 스크롤 여백으로 전달한다. 이렇게 하면 마지막
   * 카드도 CTA 위까지 완전히 올릴 수 있고, 이미지/카드 z-index 를 건드릴 필요가 없다.
   */
  useLayoutEffect(() => {
    const scroll = scrollRef.current;
    const footer = footerRef.current;
    if (!scroll || !footer) return;

    const syncFooterReserve = () => {
      scroll.style.setProperty(
        "--theme-select-footer-reserve",
        `${Math.ceil(footer.getBoundingClientRect().height)}px`
      );
    };

    syncFooterReserve();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", syncFooterReserve);
      return () => window.removeEventListener("resize", syncFooterReserve);
    }

    const observer = new ResizeObserver(syncFooterReserve);
    observer.observe(footer);
    return () => observer.disconnect();
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
      const stored = raw ? (JSON.parse(raw) as StoredPipeline) : null;
      // 라이브러리 모드에서는 부모가 내려준 발행 메타데이터가 정본이다 —
      // sessionStorage 가 비었거나(쿼터 실패) 이전 업로드 세션의 잔재를 담고
      // 있어도 이 값이 이긴다.
      const pipeline = applyLibraryOverride(stored, isLibraryFlow, libraryPublication);
      setIsLibrarySource(isLibraryFlow || pipeline?.generation_source === "library");
    } catch {
      setIsLibrarySource(isLibraryFlow);
    }
  }, [isLibraryFlow, libraryPublication]);

  const selectTheme = useCallback((theme: MemorialTheme) => {
    setHighlightTheme(theme.id);
  }, []);

  const activeTheme = highlightTheme ?? selectedTheme;
  const previewTheme = activeTheme != null ? getMemorialTheme(activeTheme) : null;
  const previewIsOriginal = previewTheme?.themeKey === ORIGINAL_PHOTO_THEME_KEY;
  const previewBgVideo = getEffectiveBgVideo(previewTheme);
  /**
   * 원본을 골랐는데 보여 줄 사진이 없다.
   *
   * 조용히 넘어가게 두지 않는다 — 빈 원본이 그대로 유료 생성에 들어가고,
   * 고객은 결제 뒤에야 알게 된다.
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

  /**
   * 전체 카드 목록을 Included/Owned/Premium/Custom 으로 묶는다. 판정 자체는
   * lib/theme-groups.ts 의 순수 함수가 하고, 여기서는 필터 알약이 고를 목록만
   * 만든다 — node --test 로 그룹핑 규칙을 따로 덮을 수 있다.
   */
  const { ownedThemes, lockedThemes, customThemes } = useMemo(
    () => groupPremiumThemes(premiumMemorialThemes, ownership.offers),
    [ownership.offers]
  );

  const cards = useMemo<ThemeCard[]>(
    () => [
      ...freeMemorialThemes.map((theme) => ({ theme, group: "included" as const })),
      ...ownedThemes.map((theme) => ({ theme, group: "owned" as const })),
      ...lockedThemes.map((theme) => ({ theme, group: "premium" as const })),
      ...customThemes.map((theme) => ({ theme, group: "custom" as const })),
    ],
    [ownedThemes, lockedThemes, customThemes]
  );

  const visibleCards = filter === "all" ? cards : cards.filter((c) => c.group === filter);

  const filterLabel = (id: ThemeFilter): string =>
    id === "all"
      ? tc.filterAll
      : id === "included"
        ? tc.filterIncluded
        : id === "owned"
          ? tc.filterOwned
          : id === "premium"
            ? tc.filterPremium
            : tc.filterCustom;

  const hasPreparedPet = Boolean(cutoutImage);
  const stepperSteps: CreateFlowStepView[] = useMemo(() => {
    const statusLabel = { complete: tc.stepComplete, current: tc.stepCurrent, upcoming: tc.stepUpcoming };
    const label: Record<string, string> = {
      uploadPhotos: tc.stepUploadPhotos,
      preparePet: tc.stepPreparePet,
      chooseTheme: tc.stepChooseTheme,
      previewCreate: tc.stepPreviewCreate,
      playOnBeam: tc.stepPlayOnBeam,
    };
    return computeCreateFlowSteps(hasPreparedPet).map((step) => ({
      id: step.id,
      label: label[step.id],
      status: step.status,
      statusLabel: statusLabel[step.status],
    }));
  }, [hasPreparedPet, tc]);
  const currentStepIndex = Math.max(
    0,
    stepperSteps.findIndex((s) => s.status === "current")
  );

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
  // 만들지 않았다면 카드는 자리표시자(플레이스홀더)로 보이고, 실제 거절은
  // 다음 화면의 장면 준비에서 일어난다.

  return (
    <div className="theme-selection-screen flex h-full min-h-0 flex-col overflow-hidden">
      <header className="eb-screen-header theme-select__header">
        <div className="eb-screen-header__leading">
          <BackButton onClick={onBack} label={memorialT(language).common.back} />
        </div>
        <h1 className="eb-screen-header__title theme-select__title">
          {isLibraryFlow ? tc.changeThemeContext : tc.title}
        </h1>
        <div className="eb-screen-header__trailing" />
      </header>

      <div className="theme-select__body flex-1 min-h-0 flex flex-col overflow-hidden">
        {/* Create 흐름에서만 — My Library 의 "테마 변경"에는 업로드/처리 단계가
            없다. 데스크톱은 세로 목록, 모바일은 압축 바(CreateFlowStepper 안의
            CSS 컨테이너 쿼리가 정한다). */}
        {!isLibraryFlow ? (
          <CreateFlowStepper
            steps={stepperSteps}
            ariaLabel={tc.stepperAriaLabel}
            compactSummary={tc.stepperCompactPrefix(currentStepIndex + 1, stepperSteps.length)}
            className="theme-select__stepper"
          />
        ) : null}

        <div className="theme-select__main flex-1 min-h-0 flex flex-col overflow-hidden">
          <div
            ref={scrollRef}
            className="theme-select__scroll hide-scrollbar min-h-0 flex-1 overflow-y-auto"
          >
            <div className="theme-select__intro">
              {!isLibraryFlow ? <p className="theme-select__subtitle eb-caption">{tc.subtitle}</p> : null}

              {ownership.balance != null ? (
                <div className="theme-select__balance-row">
                  <span className="eb-balance-pill theme-select__balance-pill">
                    <span className="eb-balance-pill__label theme-select__balance-pill-label">
                      {tc.balanceHeading}
                    </span>
                    {ownership.balance}
                  </span>
                </div>
              ) : null}

              {/* My Library 경로는 정적 누끼(cutoutImage) 없이 발행된 영상만 갖고
                  들어온다 — generation_source === "library" 면 업로드 갱신을
                  요구하는 이 경고는 틀린 안내이므로 보이지 않는다. */}
              {!cutoutImage && !isLibrarySource ? (
                <div className="theme-select__notice eb-notice eb-notice--warning">{tc.cutoutMissing}</div>
              ) : null}

              {originalMissing ? (
                <div role="alert" className="theme-select__notice eb-notice eb-notice--warning">
                  {tc.originalMissing}
                </div>
              ) : null}
            </div>

            <div className="theme-select__filters" role="toolbar" aria-label={tc.title}>
              {FILTER_ORDER.map((id) => (
                <button
                  key={id}
                  type="button"
                  aria-pressed={filter === id}
                  className={`eb-pill theme-select__filter-pill${filter === id ? " eb-pill--active" : ""}`}
                  onClick={() => setFilter(id)}
                >
                  {filterLabel(id)}
                </button>
              ))}
            </div>

            {visibleCards.length > 0 ? (
              <div className="theme-select__grid">
                {visibleCards.map(({ theme, group }) => (
                  <ThemeGridCard
                    key={theme.id}
                    theme={theme}
                    group={group}
                    selected={activeTheme === theme.id}
                    offers={ownership.offers}
                    themeLabel={themeLabel}
                    originalPhoto={originalPhoto}
                    hasCustomAsset={theme.requiresGeneration ? Boolean(getEffectiveBgVideo(theme)) : true}
                    tc={tc}
                    onSelect={() => selectTheme(theme)}
                  />
                ))}
              </div>
            ) : (
              <p className="theme-select__empty eb-caption">{tc.filterEmpty}</p>
            )}
          </div>

          <div
            ref={footerRef}
            className="theme-selection-footer theme-select__footer shrink-0 relative z-20"
          >
            {ownership.error ? (
              <p role="alert" className="eb-field-error text-center">
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
              <p className="eb-caption text-center">
                {tc.balanceLabel(ownership.balance)}
                {shortfall != null ? (
                  <span className="theme-select__shortfall">{tc.needMoreCredits(shortfall)}</span>
                ) : null}
              </p>
            ) : null}
            <button
              type="button"
              onClick={() => void handlePrimaryAction()}
              disabled={primaryDisabled}
              className="cta-gold eb-btn-label w-full"
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
        </div>
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
