"use client";

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  Coins,
  Crown,
  MoreVertical,
  Pause,
  PawPrint,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  Wind,
} from "lucide-react";
import { PreviewScreen } from "./preview-screen";
import { ThemeSelectionScreen } from "./theme-selection-screen";
import { ETERNAL_BEAM_PIPELINE_KEY, type StoredPipeline } from "./ai-processing-screen";
import { getMemorialTheme, DEFAULT_THEME_ID } from "./themes";
import { memorialT } from "./memorial-i18n";
import { ScreenHeader } from "@/components/ui/screen-header";
import { LoadingState, ErrorState } from "@/components/ui/feedback-states";
import { StatusBadge, type StatusTone } from "@/components/ui/status-badge";
import { resolvePairedDeviceId, sendDeviceCommand } from "@/lib/device-command-api";
import { CreditPackSheet } from "./credit-pack-sheet";
import { fetchWallet } from "@/lib/credits-api";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { deriveMembershipState, type MembershipState, type SubscriptionStatus } from "@/lib/membership";
import { PremiumAssetsProvider } from "./premium-assets-context";
import { BehaviorLibrary } from "./behavior-library";
import { LibraryMotionPoster } from "./library-motion-poster";
import { placeCardMenu, type MenuPlacement } from "@/lib/library-card-menu";
import { readActiveGeneration } from "@/lib/generation-resume";
import {
  fetchLibraryForPet,
  fetchLibraryPetIds,
  type LibraryMotion,
  type LibraryPetId,
} from "@/lib/my-library-api";
import type { LibraryPublication } from "@/lib/library-publication";
import {
  clearLibraryFlowState,
  readLibraryFlowState,
  resolveLibraryFlowRestore,
  saveLibraryFlowState,
} from "@/lib/library-flow-state";

type LibraryStep = "library" | "themes" | "preview";

/** One registered pet's library lookup, tracked independently so a single
 *  pet's failure never blanks out the others (each section owns its own
 *  loading/error/retry). */
type PetLibraryState =
  | { status: "loading" }
  | {
      status: "ready";
      motions: LibraryMotion[];
      subscriptionStatus: SubscriptionStatus;
      entitled: boolean;
    }
  | { status: "error"; message: string };

type SortOrder = "newest" | "oldest" | "name";
type CardStatus = "included" | "owned" | "generating" | "unavailable";

/** A motion still being generated. Not a Library row (the backend never
 *  returns unpublished work) — synthesized client-side from the same
 *  real, already-polled sources the rest of the app uses to resume work. */
interface GeneratingCard {
  key: string;
  motionId: string;
  /** Only BREATHING's free-motion resume marker points back to a real screen. */
  resumable: boolean;
}

interface MyLibraryScreenProps {
  language?: string;
  onBack: () => void;
  onComplete: () => void;
  /** Start a brand-new capture (header CTA, bottom banner, "Add Another Pet"). */
  onCreateNew: () => void;
  /** Opens Account with Membership focused, preserving Library as the return context. */
  onOpenMembership: () => void;
}

function titleForPet(pet: { petId: string }): string {
  return pet.petId.replace(/^pet_/, "") || pet.petId;
}

function contentIdOfPet(pet: LibraryPetId): string {
  return pet.contentId || pet.petId.replace(/^pet_/, "");
}

function storePreviewPipeline(pet: LibraryPetId, motion: LibraryMotion): void {
  const contentId = contentIdOfPet(pet);
  const pipeline: StoredPipeline = {
    content_id: contentId,
    cutout_display_url: "",
    dog_only_nobg_url: "",
    idle_video_url: motion.url ?? "",
    action_video_url: "",
    // 서버가 이미 돌려주는 값이다 — 여기서 지어내지 않는다. 하드코딩된 false 는
    // 구운 자산을 레거시로 잘못 재생시켜 배경을 두 번 적용시켰다.
    background_baked: motion.backgroundBaked,
    delivery_format: motion.deliveryFormat,
    generation_source: "library",
    qa_decision: "PASS",
  };
  // 쓰기가 실패해도(쿼터·프라이빗 모드) 화면은 멈추지 않는다 — 아래 화면들은
  // libraryPublication prop 을 정본으로 쓰므로 이 저장은 레거시 화면(기기 재생
  // 등)을 위한 **보조** 경로일 뿐이다.
  try {
    sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(pipeline));
  } catch {
    /* ignore quota */
  }
}

function toLibraryPublication(pet: LibraryPetId, motion: LibraryMotion): LibraryPublication {
  return {
    petId: pet.petId,
    idleVideoUrl: motion.url ?? "",
    deliveryFormat: motion.deliveryFormat,
    backgroundBaked: motion.backgroundBaked,
    motionId: motion.motionId,
  };
}

function statusForMotion(motion: LibraryMotion): CardStatus {
  if (motion.accessState === "unavailable") return "unavailable";
  return motion.ownershipType === "included" ? "included" : "owned";
}

function toneForStatus(status: CardStatus): StatusTone {
  if (status === "included") return "success";
  if (status === "owned") return "owned";
  if (status === "generating") return "generating";
  return "error";
}

function sortMotions(motions: LibraryMotion[], order: SortOrder): LibraryMotion[] {
  const copy = [...motions];
  if (order === "name") {
    copy.sort((a, b) => (a.displayName ?? a.motionId).localeCompare(b.displayName ?? b.motionId));
    return copy;
  }
  const at = (m: LibraryMotion) => Date.parse(m.publishedAt ?? m.generatedAt ?? "") || 0;
  copy.sort((a, b) => (order === "newest" ? at(b) - at(a) : at(a) - at(b)));
  return copy;
}

function formatMotionDate(iso: string | null, lang: string): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  try {
    return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "ko-KR", {
      year: "numeric",
      month: "short",
      day: "numeric",
    }).format(d);
  } catch {
    return null;
  }
}

function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function membershipLabel(state: MembershipState, t: ReturnType<typeof memorialT>["library"]): string {
  switch (state.phase) {
    case "active":
      return t.memberActive;
    case "grace":
      return t.memberGrace;
    case "lapsed":
      return t.memberLapsed;
    default:
      return t.memberNone;
  }
}

// ── Motion card ──────────────────────────────────────────────────────────────

/** 세 점 메뉴 — document.body 포털에 fixed 로 띄운다. 카드는 overflow:hidden 이라
 *  카드 안에 두면 아래로 뻗는 메뉴가 잘린다. 버튼 위치 기준으로 놓고, 아래 공간이
 *  모자라면 위로 뒤집는다. 바깥 클릭·Escape·스크롤·리사이즈로 닫힌다. */
function MotionCardMenu({
  label,
  actionLabel,
  onAction,
}: {
  label: string;
  actionLabel: string;
  onAction: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [placed, setPlaced] = useState<{ top: number; left: number; placement: MenuPlacement } | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback((restoreFocus = false) => {
    setOpen(false);
    setPlaced(null);
    if (restoreFocus) buttonRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    const button = buttonRef.current;
    const menu = menuRef.current;
    if (!button || !menu) return;
    const rect = button.getBoundingClientRect();
    setPlaced(
      placeCardMenu({
        anchor: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
        menuWidth: menu.offsetWidth,
        menuHeight: menu.offsetHeight,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
      }),
    );
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (menuRef.current?.contains(target) || buttonRef.current?.contains(target)) return;
      close();
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        close(true);
      }
    };
    const onViewportChange = () => close();
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [open, close]);

  return (
    <div className="my-library__card-menu">
      <button
        ref={buttonRef}
        type="button"
        className="my-library__card-overflow"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <MoreVertical className="w-4 h-4" />
      </button>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              className={`my-library__card-menu-list my-library__card-menu-list--${placed?.placement ?? "below"}`}
              role="menu"
              aria-label={label}
              style={placed ? { top: placed.top, left: placed.left } : { visibility: "hidden" }}
            >
              <button
                type="button"
                role="menuitem"
                className="my-library__card-menu-item"
                onClick={() => {
                  close();
                  onAction();
                }}
              >
                {actionLabel}
              </button>
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}

/** 카드 미디어: 탐색 버튼(카드 본체 → 테마 선택)과 **형제** 재생 버튼(인라인
 *  미리보기). Play 를 탐색 버튼 안에 두면 클릭이 버블링돼 테마 화면이 열린다 —
 *  그래서 같은 컨테이너의 형제로 두고, 클릭은 preventDefault + stopPropagation 으로
 *  카드 탐색에 닿지 않게 한다. */
function MotionCard({
  motion,
  status,
  dateLabel,
  t,
  playing,
  onSelect,
  onTogglePlay,
}: {
  motion: LibraryMotion;
  status: CardStatus;
  dateLabel: string | null;
  t: ReturnType<typeof memorialT>["library"];
  playing: boolean;
  onSelect: () => void;
  onTogglePlay: () => void;
}) {
  const [duration, setDuration] = useState<string | null>(null);
  const clickable = status === "included" || status === "owned";
  const name = (motion.displayName ?? motion.motionId).toLowerCase();
  const previewable = clickable && Boolean(motion.url);

  return (
    <div className={`my-library__card my-library__card--${status}${playing ? " my-library__card--playing" : ""}`}>
      <div className="my-library__card-media">
        <button
          type="button"
          className="my-library__card-hit"
          disabled={!clickable}
          onClick={clickable ? onSelect : undefined}
          aria-label={name}
        >
          {motion.url ? (
            <LibraryMotionPoster
              src={motion.url}
              deliveryFormat={motion.deliveryFormat}
              playing={playing}
              onDuration={(d) => setDuration(formatDuration(d))}
            />
          ) : (
            <span className="my-library__card-media-fallback" aria-hidden>
              <Wind className="w-6 h-6" />
            </span>
          )}
          <StatusBadge tone={toneForStatus(status)} className="my-library__card-status">
            {status === "included" ? t.statusIncluded : status === "owned" ? t.statusOwned : t.statusUnavailable}
          </StatusBadge>
          {duration ? <span className="my-library__card-duration">{duration}</span> : null}
        </button>
        {previewable ? (
          <button
            type="button"
            className={`my-library__card-play${playing ? " my-library__card-play--active" : ""}`}
            aria-pressed={playing}
            aria-label={playing ? t.previewPause(name) : t.previewPlay(name)}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onTogglePlay();
            }}
          >
            {playing ? <Pause className="w-5 h-5" fill="currentColor" /> : <Play className="w-5 h-5" fill="currentColor" />}
          </button>
        ) : null}
      </div>
      <div className="my-library__card-body">
        <div className="my-library__card-text">
          <p className="my-library__card-name">{name}</p>
          {dateLabel ? <p className="my-library__card-meta">{dateLabel}</p> : null}
        </div>
        {clickable ? (
          <MotionCardMenu label={t.overflowMenu(name)} actionLabel={t.playOnBeamAction} onAction={onSelect} />
        ) : null}
      </div>
    </div>
  );
}

function GeneratingMotionCard({
  card,
  t,
  onResume,
}: {
  card: GeneratingCard;
  t: ReturnType<typeof memorialT>["library"];
  onResume: () => void;
}) {
  const name = card.motionId.toLowerCase();
  return (
    <div className="my-library__card my-library__card--generating">
      <button
        type="button"
        className="my-library__card-media"
        disabled={!card.resumable}
        onClick={card.resumable ? onResume : undefined}
        aria-label={name}
      >
        <span className="my-library__card-media-fallback my-library__card-media-fallback--generating" aria-hidden>
          <RefreshCw className="w-6 h-6 my-library__spin" />
        </span>
        <StatusBadge tone="generating" className="my-library__card-status">
          {t.statusGenerating}
        </StatusBadge>
      </button>
      <div className="my-library__card-body">
        <div className="my-library__card-text">
          <p className="my-library__card-name">{name}</p>
          {card.resumable ? <p className="my-library__card-meta">{t.resumeGenerationAction}</p> : null}
        </div>
      </div>
    </div>
  );
}

// ── Pet section ──────────────────────────────────────────────────────────────

interface PetSectionProps {
  pet: LibraryPetId;
  state: PetLibraryState;
  label: string;
  t: ReturnType<typeof memorialT>["library"];
  language: string;
  motionFilter: string;
  sortOrder: SortOrder;
  /** 지금 인라인 재생 중인 카드(motion.id) — 화면 전체에서 하나뿐이다. */
  activePlaybackId: string | null;
  onTogglePlayback: (motionId: string) => void;
  onRetry: () => void;
  onSelectMotion: (pet: LibraryPetId, motion: LibraryMotion) => void;
  onResumeGeneration: (pet: LibraryPetId) => void;
  onOpenMembership: () => void;
}

/** Wraps the section in its own premium-assets poll (Phase 4's per-pet
 *  discovery contract) so "Generating" can reflect this **specific** pet's
 *  in-flight premium motions — not just whichever pet the app happens to
 *  have "active" elsewhere. */
function PetSection(props: PetSectionProps) {
  return (
    <PremiumAssetsProvider petId={props.pet.petId} enabled>
      <PetSectionInner {...props} />
    </PremiumAssetsProvider>
  );
}

function PetSectionInner({
  pet,
  state,
  label,
  t,
  language,
  motionFilter,
  sortOrder,
  activePlaybackId,
  onTogglePlayback,
  onRetry,
  onSelectMotion,
  onResumeGeneration,
  onOpenMembership,
}: PetSectionProps) {
  const breathingResume = useMemo(() => readActiveGeneration(contentIdOfPet(pet)), [pet]);

  const generatingCards = useMemo<GeneratingCard[]>(() => {
    const cards: GeneratingCard[] = [];
    if (breathingResume) {
      cards.push({ key: "breathing-resume", motionId: breathingResume.motionId || "BREATHING", resumable: true });
    }
    return motionFilter === "all" ? cards : cards.filter((c) => c.motionId === motionFilter);
  }, [breathingResume, motionFilter]);

  if (state.status === "loading") {
    return (
      <section className="my-library__pet-section" aria-busy>
        <div className="my-library__pet-head">
          <span className="my-library__pet-avatar" aria-hidden>
            <PawPrint className="w-5 h-5" />
          </span>
          <div>
            <p className="my-library__pet-name">{label}</p>
          </div>
        </div>
        <div className="my-library__cards-grid">
          {[0, 1, 2].map((i) => (
            <div key={i} className="my-library__card my-library__card--skeleton" aria-hidden />
          ))}
        </div>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="my-library__pet-section">
        <div className="my-library__pet-head">
          <span className="my-library__pet-avatar my-library__pet-avatar--error" aria-hidden>
            <AlertCircle className="w-5 h-5" />
          </span>
          <div>
            <p className="my-library__pet-name">{label}</p>
            <p className="my-library__pet-hint my-library__pet-hint--error">{t.petLoadError}</p>
          </div>
          <button type="button" className="my-library__pet-retry" onClick={onRetry} aria-label={t.retryPet(label)}>
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </section>
    );
  }

  const filtered = motionFilter === "all" ? state.motions : state.motions.filter((m) => m.motionId === motionFilter);
  const sorted = sortMotions(filtered, sortOrder);

  return (
    <div className="my-library__pet-stack">
      {sorted.length > 0 ? (
        <section className="my-library__pet-section">
          <div className="my-library__pet-head">
            <span className="my-library__pet-avatar" aria-hidden>
              <PawPrint className="w-5 h-5" />
            </span>
            <div>
              <p className="my-library__pet-name">{label}</p>
              <p className="my-library__pet-hint">{t.motionCount(sorted.length)}</p>
            </div>
          </div>
          <div className="my-library__cards-grid">
            {sorted.map((motion) => (
              <MotionCard
                key={motion.id}
                motion={motion}
                status={statusForMotion(motion)}
                dateLabel={formatMotionDate(motion.publishedAt ?? motion.generatedAt, language)}
                t={t}
                playing={activePlaybackId === motion.id}
                onSelect={() => onSelectMotion(pet, motion)}
                onTogglePlay={() => onTogglePlayback(motion.id)}
              />
            ))}
          </div>
        </section>
      ) : null}

      {generatingCards.length > 0 ? (
        <section className="my-library__generation-resume">
          <div className="my-library__available-head">
            <div>
              <h3 className="my-library__available-title">{memorialT(language).behaviors.availableTitle}</h3>
              <p className="my-library__available-hint">{memorialT(language).behaviors.generationKeepsGoing}</p>
            </div>
          </div>
          <div className="my-library__cards-grid">
            {generatingCards.map((card) => (
              <GeneratingMotionCard key={card.key} card={card} t={t} onResume={() => onResumeGeneration(pet)} />
            ))}
          </div>
        </section>
      ) : null}

      <BehaviorLibrary
        petId={pet.petId}
        enabled
        language={language}
        onOpenMembership={onOpenMembership}
      />
    </div>
  );
}

// ── Screen ───────────────────────────────────────────────────────────────────

export function MyLibraryScreen({
  language = "ko",
  onBack,
  onComplete: _onComplete,
  onCreateNew,
  onOpenMembership,
}: MyLibraryScreenProps) {
  const t = memorialT(language).library;
  const common = memorialT(language).common;

  // Phase 9 — 새로고침 재개. 지난 방문에서 어디까지 갔었는지(step/pet/motion/
  // theme)를 지연 초기화로 한 번만 읽는다. themes/preview 로 넘어간 뒤의
  // 새로고침만 의미가 있다 — 브라우징(library 단계)은 복원할 상태가 없는
  // 단일 그리드다.
  const [restoredFlow] = useState(() => readLibraryFlowState());
  const [flowRestorePending, setFlowRestorePending] = useState(() => restoredFlow != null);
  const [step, setStep] = useState<LibraryStep>(() => restoredFlow?.step ?? "library");

  const [petIds, setPetIds] = useState<LibraryPetId[] | null>(null);
  const [registryError, setRegistryError] = useState<string | null>(null);
  const [petStates, setPetStates] = useState<Record<string, PetLibraryState>>({});

  const [selectedPet, setSelectedPet] = useState<LibraryPetId | null>(null);
  const [selectedMotion, setSelectedMotion] = useState<LibraryMotion | null>(null);
  const [selectedTheme, setSelectedTheme] = useState<number>(() => restoredFlow?.themeId ?? DEFAULT_THEME_ID);
  const [error, setError] = useState<string | null>(null);

  const [petFilter, setPetFilter] = useState<string>("all");
  const [motionFilter, setMotionFilter] = useState<string>("all");
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");

  const [wallet, setWallet] = useState<{ balance: number } | null>(null);
  const [showCreditSheet, setShowCreditSheet] = useState(false);

  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const loadPetMotions = useCallback((petId: string) => {
    setPetStates((prev) => ({ ...prev, [petId]: { status: "loading" } }));
    fetchLibraryForPet(petId)
      .then((result) => {
        if (cancelledRef.current) return;
        setPetStates((prev) => ({
          ...prev,
          [petId]: {
            status: "ready",
            motions: result.motions,
            subscriptionStatus: result.subscriptionStatus,
            entitled: result.entitled,
          },
        }));
      })
      .catch((cause) => {
        if (cancelledRef.current) return;
        setPetStates((prev) => ({
          ...prev,
          [petId]: { status: "error", message: cause instanceof Error ? cause.message : String(cause) },
        }));
      });
  }, []);

  const loadRegistry = useCallback(() => {
    setPetIds(null);
    setRegistryError(null);
    void fetchLibraryPetIds()
      .then((ids) => {
        if (cancelledRef.current) return;
        setPetIds(ids);
        // 각 펫의 라이브러리 조회는 독립적으로 돈다 — 하나가 실패하거나 늦어도
        // 나머지 카드는 그대로 채워진다.
        ids.forEach((id) => loadPetMotions(id.petId));
      })
      .catch((cause) => {
        if (cancelledRef.current) return;
        setRegistryError(cause instanceof Error ? cause.message : String(cause));
      });
  }, [loadPetMotions]);

  useEffect(() => {
    loadRegistry();
  }, [loadRegistry]);

  // Beam Credits — 읽기 전용, 실패해도 화면은 그대로 동작한다(칩만 숨는다).
  useEffect(() => {
    let cancelled = false;
    void getPremiumAccessToken().then(async (auth) => {
      if (!auth.token || cancelled) return;
      try {
        const w = await fetchWallet({ accessToken: auth.token });
        if (!cancelled) setWallet({ balance: w.balance });
      } catch {
        /* 칩을 숨기는 것으로 충분하다 — 라이브러리 조회를 막지 않는다 */
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase 9 — 새로고침 재개 판정.
  useEffect(() => {
    if (!flowRestorePending) return;
    const pet = (petIds ?? []).find((p) => p.petId === restoredFlow?.petId) ?? null;
    const petState = pet ? petStates[pet.petId] : undefined;
    const motionsForPet =
      petState?.status === "ready"
        ? petState.motions.map((m) => ({ motionId: m.id }))
        : petState?.status === "error"
          ? []
          : null;
    const result = resolveLibraryFlowRestore(restoredFlow, petIds, motionsForPet);
    if (result.status === "pending") return;

    const finish = (nextStep: LibraryStep) => {
      setStep(nextStep);
      setFlowRestorePending(false);
    };

    if (result.status === "none") {
      finish("library");
      return;
    }
    if (result.status === "restore-library") {
      clearLibraryFlowState();
      finish("library");
      return;
    }
    if (!pet || petState?.status !== "ready") {
      finish("library");
      return;
    }

    const motion = result.motionId ? petState.motions.find((m) => m.id === result.motionId) : null;
    if (!motion) {
      finish("library");
      return;
    }

    storePreviewPipeline(pet, motion);
    setSelectedPet(pet);
    setSelectedMotion(motion);
    if (result.themeId != null) setSelectedTheme(result.themeId);
    finish(result.step);
  }, [flowRestorePending, restoredFlow, petIds, petStates]);

  // 정상 사용(재개 판정이 끝난 뒤)에는 매 선택 변화를 계속 적어 둔다.
  useEffect(() => {
    if (flowRestorePending) return;
    saveLibraryFlowState({
      step,
      petId: selectedPet?.petId ?? "",
      motionId: selectedMotion?.id ?? null,
      themeId: selectedTheme,
    });
  }, [flowRestorePending, step, selectedPet, selectedMotion, selectedTheme]);

  const currentTheme = useMemo(() => getMemorialTheme(selectedTheme), [selectedTheme]);
  const libraryPublication = useMemo(
    () => (selectedPet && selectedMotion ? toLibraryPublication(selectedPet, selectedMotion) : null),
    [selectedPet, selectedMotion]
  );

  const resolvedPetIds = petIds ?? [];

  const membership = useMemo<MembershipState | null>(() => {
    const ready = resolvedPetIds
      .map((id) => petStates[id.petId])
      .find((s): s is Extract<PetLibraryState, { status: "ready" }> => s?.status === "ready");
    if (!ready) return null;
    return deriveMembershipState({ status: ready.subscriptionStatus, entitled: ready.entitled, hasAuth: true });
  }, [resolvedPetIds, petStates]);

  const allMotionIds = useMemo(() => {
    const ids = new Set<string>();
    for (const id of resolvedPetIds) {
      const s = petStates[id.petId];
      if (s?.status === "ready") s.motions.forEach((m) => ids.add(m.motionId));
    }
    return Array.from(ids).sort();
  }, [resolvedPetIds, petStates]);

  // 인라인 미리보기는 화면 전체에서 카드 하나만 — 다른 카드의 Play 는 이전 카드를
  // 멈추고, 같은 카드의 Play 는 토글이다. 라이브러리를 떠나면(테마/프리뷰) 멈춘다.
  const [activePlaybackId, setActivePlaybackId] = useState<string | null>(null);
  const togglePlayback = useCallback((motionId: string) => {
    setActivePlaybackId((current) => (current === motionId ? null : motionId));
  }, []);
  useEffect(() => {
    if (step !== "library") setActivePlaybackId(null);
  }, [step]);

  const selectMotion = useCallback((pet: LibraryPetId, motion: LibraryMotion) => {
    // 발행 상태를 테마 화면에 들어가기 **전에** 동기적으로 써 둔다 — useEffect
    // 로 쓰면 자식(ThemeSelectionScreen)의 마운트 이펙트가 부모보다 먼저
    // 돌아 아직 못 읽는다.
    storePreviewPipeline(pet, motion);
    setSelectedPet(pet);
    setSelectedMotion(motion);
    setStep("themes");
  }, []);

  const resumeGeneration = useCallback(() => {
    // BREATHING 재개는 앱 전체의 기존 재개 진입점과 같다 — Preview 화면이
    // generation-resume.ts 마커를 읽어 스스로 이어 그린다.
    clearLibraryFlowState();
    setStep("preview");
  }, []);

  if (flowRestorePending) {
    return (
      <div className="hologram-bg-active memorial-screen-shell my-library h-full flex flex-col min-h-0">
        <ScreenHeader title={t.title} onBack={onBack} backLabel={common.back} />
        <div className="my-library__scroll flex-1 overflow-y-auto min-h-0 px-6 pb-[var(--eb-footer-bottom)]">
          <LoadingState label={t.loading} />
        </div>
      </div>
    );
  }

  if (step === "preview" && selectedPet && selectedMotion && currentTheme) {
    return (
      <PreviewScreen
        cutoutImage={null}
        isLibraryFlow
        libraryPublication={libraryPublication}
        selectedTheme={selectedTheme}
        language={language}
        settings={{ scale: 1, posX: 0, posY: 0 }}
        deliveryMode="device"
        onSettingsChange={() => {}}
        // Library 갈래는 "완료" 개념이 없다 — 발행된 영상은 이미 완성되어
        // 있고, 여기서 할 수 있는 전부는 Beam으로 보내는 것뿐이다(아래
        // onPlayOnBeam). PreviewScreen 은 hasIdle=true(라이브러리는 항상
        // 그렇다)일 때 onComplete 을 호출하지 않는 새 액션 레일을 그린다.
        onComplete={() => {}}
        onPlayOnBeam={async () => {
          // 두 명령 모두의 결과를 확인한다 — 예전에는 여기서 await 만 하고
          // 실패를 무시한 채 무조건 전송 성공으로 취급했다.
          //
          // P0 — beam-001 은 더 이상 무조건 쓰지 않는다. 새 게이트웨이는
          // 아직 사용자별 페어링이 없으므로(resolvePairedDeviceId), 명시적
          // 데모 플래그 밖에서는 보낼 실제 device_id 가 없다 — 그 경우
          // 명령을 하나도 내보내지 않고 진짜 실패로 돌려준다.
          const deviceId = resolvePairedDeviceId();
          if (!deviceId) {
            return { ok: false, reason: "no_paired_device" };
          }
          const themeResult = await sendDeviceCommand({
            device_id: deviceId,
            event: "theme_play",
            theme_id: currentTheme.themeKey,
          });
          if (!themeResult.ok) return { ok: false, reason: themeResult.reason, status: themeResult.status };
          const petResult = await sendDeviceCommand({
            device_id: deviceId,
            event: "pet_asset",
            pet_id: selectedPet.petId,
            motion_id: selectedMotion.motionId,
            spawn_vfx: "heart",
          });
          return petResult.ok
            ? { ok: true, commandId: petResult.command_id }
            : { ok: false, reason: petResult.reason, status: petResult.status };
        }}
        onChangeMotion={
          petStates[selectedPet.petId]?.status === "ready" &&
          (petStates[selectedPet.petId] as { status: "ready"; motions: LibraryMotion[] }).motions.length > 1
            ? () => setStep("library")
            : undefined
        }
        onBack={() => setStep("themes")}
      />
    );
  }

  if (step === "themes") {
    return (
      <ThemeSelectionScreen
        cutoutImage={null}
        cutoutReadiness="ready"
        isLibraryFlow
        libraryPublication={libraryPublication}
        selectedTheme={selectedTheme}
        language={language}
        onSelectTheme={setSelectedTheme}
        onSelectCustomBackground={() => setError("Custom backgrounds are not available in My Library yet.")}
        onContinue={(themeId) => {
          if (!selectedPet || !selectedMotion) return;
          setSelectedTheme(themeId);
          setStep("preview");
        }}
        onBack={() => setStep("library")}
      />
    );
  }

  const showLoading = petIds === null && !registryError;
  const anyPetError = resolvedPetIds.some((id) => petStates[id.petId]?.status === "error");
  const allResolved = resolvedPetIds.every((id) => petStates[id.petId] && petStates[id.petId].status !== "loading");
  const anyVisible = resolvedPetIds.some((id) => {
    const s = petStates[id.petId];
    return s?.status === "ready" && s.motions.length > 0;
  });
  const showEmptyState =
    petIds !== null && resolvedPetIds.length > 0 && allResolved && !anyVisible && !anyPetError;

  const visiblePetIds = petFilter === "all" ? resolvedPetIds : resolvedPetIds.filter((p) => p.petId === petFilter);

  return (
    <div className="hologram-bg-active memorial-screen-shell my-library h-full flex flex-col min-h-0">
      <ScreenHeader title={t.title} onBack={onBack} backLabel={common.back} />

      <div className="my-library__scroll flex-1 overflow-y-auto min-h-0 px-6 pb-[var(--eb-footer-bottom)]">
        {error ? (
          <div role="alert" className="eb-notice eb-notice--warning mb-4">
            {error}
          </div>
        ) : null}

        <div className="my-library__hero">
          <div className="my-library__hero-text">
            <h1 className="my-library__title">{t.title}</h1>
            <p className="my-library__subtitle">{t.subtitle}</p>
          </div>
          <div className="my-library__hero-actions">
            {wallet ? (
              <button
                type="button"
                className="my-library__chip my-library__chip--credits"
                onClick={() => setShowCreditSheet(true)}
              >
                <Coins className="w-4 h-4" aria-hidden />
                <span className="my-library__chip-value">{wallet.balance}</span>
                <span className="my-library__chip-label">{t.beamCredits}</span>
                <Plus className="w-3.5 h-3.5 my-library__chip-add" aria-hidden />
              </button>
            ) : null}
            {membership ? (
              <button
                type="button"
                className={`my-library__chip my-library__chip--membership my-library__chip--${membership.phase}`}
                onClick={onOpenMembership}
              >
                <Crown className="w-4 h-4" aria-hidden />
                <span className="my-library__chip-label">{membershipLabel(membership, t)}</span>
              </button>
            ) : null}
            <button type="button" className="eb-btn eb-btn--primary cta-gold my-library__create-btn" onClick={onCreateNew}>
              <Plus className="w-4 h-4" aria-hidden /> {t.createNew}
            </button>
          </div>
        </div>

        {resolvedPetIds.length > 0 ? (
          <div className="my-library__filters" role="toolbar" aria-label={t.title}>
            <button
              type="button"
              className={`my-library__filter-chip${petFilter === "all" ? " my-library__filter-chip--active" : ""}`}
              onClick={() => setPetFilter("all")}
            >
              {t.allPets}
            </button>
            {resolvedPetIds.map((id) => (
              <button
                key={id.petId}
                type="button"
                className={`my-library__filter-chip${petFilter === id.petId ? " my-library__filter-chip--active" : ""}`}
                onClick={() => setPetFilter(id.petId)}
              >
                <PawPrint className="w-3.5 h-3.5" aria-hidden /> {titleForPet(id)}
              </button>
            ))}
            {allMotionIds.length > 0 ? (
              <select
                className="my-library__select"
                value={motionFilter}
                onChange={(e) => setMotionFilter(e.target.value)}
                aria-label={t.allMotions}
              >
                <option value="all">{t.allMotions}</option>
                {allMotionIds.map((id) => (
                  <option key={id} value={id}>
                    {id.toLowerCase()}
                  </option>
                ))}
              </select>
            ) : null}
            <select
              className="my-library__select"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as SortOrder)}
              aria-label={t.sortNewest}
            >
              <option value="newest">{t.sortNewest}</option>
              <option value="oldest">{t.sortOldest}</option>
              <option value="name">{t.sortByName}</option>
            </select>
          </div>
        ) : null}

        {showLoading ? (
          <LoadingState label={t.loading} />
        ) : registryError ? (
          <ErrorState
            title={t.registryErrorTitle}
            description={registryError || t.registryErrorBody}
            onRetry={loadRegistry}
            retryLabel={t.retry}
          />
        ) : resolvedPetIds.length === 0 ? (
          <div className="eb-feedback-state">
            <Sparkles className="w-6 h-6" style={{ color: "var(--eb-gold-text)" }} aria-hidden />
            <p className="eb-feedback-state__title">{t.emptyPetsTitle}</p>
            <p className="eb-feedback-state__description">{t.emptyPetsBody}</p>
          </div>
        ) : (
          <>
            {showEmptyState ? (
              <div className="eb-feedback-state my-library__owned-empty">
                <Sparkles className="w-6 h-6" style={{ color: "var(--eb-gold-text)" }} aria-hidden />
                <p className="eb-feedback-state__title">{t.emptyMotionsTitle}</p>
                <p className="eb-feedback-state__description">{t.emptyMotionsBody}</p>
              </div>
            ) : null}
            <div className="my-library__sections">
              {visiblePetIds.map((id) => (
                <PetSection
                  key={id.petId}
                  pet={id}
                  state={petStates[id.petId] ?? { status: "loading" }}
                  label={titleForPet(id)}
                  t={t}
                  language={language}
                  motionFilter={motionFilter}
                  sortOrder={sortOrder}
                  activePlaybackId={activePlaybackId}
                  onTogglePlayback={togglePlayback}
                  onRetry={() => loadPetMotions(id.petId)}
                  onSelectMotion={selectMotion}
                  onResumeGeneration={resumeGeneration}
                  onOpenMembership={onOpenMembership}
                />
              ))}
            </div>
          </>
        )}

        {petIds !== null && !registryError ? (
          <div className="my-library__bottom-row">
            <button type="button" className="my-library__add-pet" onClick={onCreateNew}>
              <span className="my-library__add-pet-icon" aria-hidden>
                <Plus className="w-5 h-5" />
              </span>
              <span className="my-library__add-pet-body">
                <span className="my-library__add-pet-title">{t.addAnotherPet}</span>
                <span className="my-library__add-pet-hint">{t.addAnotherPetHint}</span>
              </span>
              <span className="eb-btn eb-btn--secondary my-library__add-pet-cta" aria-hidden>
                <Plus className="w-4 h-4" /> {t.addPetCta}
              </span>
            </button>

            <div className="my-library__banner">
              <div className="my-library__banner-text">
                <p className="my-library__banner-title">{t.bannerTitle}</p>
                <p className="my-library__banner-body">{t.bannerBody}</p>
              </div>
              <button type="button" className="eb-btn eb-btn--primary cta-gold my-library__banner-cta" onClick={onCreateNew}>
                <Plus className="w-4 h-4" aria-hidden /> {t.bannerCta}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      {showCreditSheet ? (
        <CreditPackSheet balance={wallet?.balance ?? null} onClose={() => setShowCreditSheet(false)} />
      ) : null}
    </div>
  );
}
