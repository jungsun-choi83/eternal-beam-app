"use client";

import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import { motion } from "framer-motion";
import { RotateCcw, PawPrint, ChevronDown, Palette, Play } from "lucide-react";
import { BackButton } from "@/components/ui/screen-header";
import { MemorialIconButton } from "@/components/memorial/memorial-chrome";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  getMemorialTheme,
  DEFAULT_THEME_ID,
} from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { PetPhoto } from "@/components/memorial/pet-photo";
import { usePetGrounding } from "@/components/memorial/use-pet-grounding";
import { subjectTransform } from "@/lib/pet-grounding";
import {
  playbackFrameClass,
  resolveDeliveryFormat,
  shouldApplySubjectTransform,
  shouldRenderThemeBackdrop,
} from "@/lib/baked-playback";
import {
  buildCanonicalScene,
  readOriginalPhoto,
  resolveSceneBackground,
} from "@/lib/build-canonical-scene";
import { ORIGINAL_PHOTO_THEME_KEY } from "@/components/memorial/themes";
import type { CanonicalScene } from "@/lib/canonical-scene";
import {
  isSceneError,
  sceneErrorMessage,
  serverGenerationMessage,
} from "@/lib/scene-errors";
import {
  registeredIdleEvents,
  type IdleEvent,
  type PetRuntimeTrigger,
} from "@/lib/pet-runtime-events";
import { useIdleEventScheduler } from "@/components/memorial/use-idle-event-scheduler";
import { useIdleEventAssets } from "@/components/memorial/use-idle-event-assets";
import {
  PremiumAssetsProvider,
  usePremiumAssetsContext,
} from "@/components/memorial/premium-assets-context";
import { useBehaviorEligibility } from "@/components/memorial/use-behavior-eligibility";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { ensurePetRegistered } from "@/lib/pet-registry-api";
import {
  phase7GenerationEnabled,
  phase7PipelinePatch,
  retryPhase7Generation,
  runPhase7Generation,
  resumePhase7Generation,
  type Phase7Outcome,
} from "@/lib/phase7-generation-flow";
import { clearActiveGeneration, readActiveGeneration } from "@/lib/generation-resume";
import {
  submitBusinessQAFeedback,
  type GenerationRun,
} from "@/lib/generation-run-api";
import { deriveGenerationProgress, isRecoverableErrorCode } from "@/lib/generation-progress";
import { GenerationProgressScreen } from "@/components/memorial/generation-progress-screen";
import { MotionQAFeedback, type MotionQAComplaint } from "@/components/memorial/motion-qa-feedback";
import { useProcessingClock } from "@/lib/use-processing-clock";
import { getEternalBeamPetId } from "@/lib/pet-identity";
import {
  resolvePairedDeviceId,
  classifyDeviceCommandFailure,
  type DeviceCommandFailureCategory,
} from "@/lib/device-command-api";
import { getDeviceConnectionState } from "@/lib/device-connection-api";
import { onAuthStateChange } from "@/lib/supabase-auth";
import { mergeComeCloserIntoPipeline } from "@/lib/come-closer-asset";

/**
 * COME_CLOSER 발견 상태 (Phase 7I.3).
 *
 * 예전에는 dev-autogen(come-closer-autogen)의 상태 유니온을 빌려 썼다 — 그쪽의
 * "unavailable"(dev 트리거 꺼짐) 같은 값은 인증 발견 계약에는 존재하지 않는다.
 * 이제 이 화면의 상태는 발견 응답에서만 나온다: 없음 / 생성 중 / READY.
 */
type ComeCloserDiscoveryState = "idle" | "generating" | "ready";
import { recognizeTap, type TapPoint } from "@/lib/double-tap";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import { resolveSelectedThemeId } from "@/lib/theme-selection-store";
import {
  getPendingCutoutMeta,
  hasRealIdleVideo,
  rehydrateCutoutFile,
} from "@/lib/pending-generation";
import { requestIdleGeneration } from "@/lib/idle-generation-request";
import { schedulePetReadyToDevice } from "@/lib/device-pet-sync";
import { applyLibraryOverride, type LibraryPublication } from "@/lib/library-publication";
import { hydrateStoredPipeline } from "@/lib/breathing-hydration";

interface PreviewScreenProps {
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
   * 원본 갈래에 쓸 **해결된 한 장.** 테마 선택 화면과 같은 값을 부모가 내려 준다.
   * 없으면 저장된 값으로 떨어진다(구버전 호출부 호환).
   */
  originalPhoto?: string | null;
  selectedTheme: number | null;
  language?: string;
  settings: { scale: number; posX: number; posY: number };
  onSettingsChange: (settings: { scale: number; posX: number; posY: number }) => void;
  /**
   * device = 발행 뒤 **이 화면에 머무르며** Play on Web / Play on Beam 을 연다.
   * shipping(프리미엄 실물) = 발행 뒤 onComplete → 배송지 입력으로.
   */
  deliveryMode?: "device" | "shipping";
  onComplete: () => void;
  onBack: () => void;
  /**
   * "Beam으로 보내기". 실제 명령 구성(theme_id/pet_id/motion_id 해석)은
   * 부모(MyLibraryScreen 또는 EternalBeamApp)가 갖고 있고, 이 화면은 전송
   * 상태(Sending/Sent/Error)만 소유한다. 두 명령이 모두 성공(sent 또는
   * pending/accepted)했을 때만 ok:true 를 돌려줘야 한다 — 그래야 "Sent to
   * Beam" 을 실제 결과에 따라서만 보여줄 수 있다. 발행이 이것을 자동으로
   * 부르는 일은 없다 — 사용자의 명시적 누름만이 기기로 나간다.
   */
  onPlayOnBeam?: () => Promise<{ ok: boolean; reason?: string; status?: number; commandId?: string }>;
  /** 발행 뒤 멤버십(설정 > 멤버십)으로. 넘기지 않으면 버튼이 그려지지 않는다. */
  onOpenMembership?: () => void;
  /**
   * "모션 변경" — 제공될 때만(둘 이상의 발행 모션이 있을 때만) 버튼을 보여준다.
   * 지금은 기기 계약이 BREATHING 만 지원해 사실상 항상 undefined 다.
   */
  onChangeMotion?: () => void;
}

function assertPreviewTheme(selectedTheme: number | null, resolvedId: number) {
  if (import.meta.env.DEV && selectedTheme != null && selectedTheme !== resolvedId) {
    console.warn(
      "[preview] selectedTheme prop",
      selectedTheme,
      "!== resolved preview theme",
      resolvedId,
      "— using resolved id from localStorage sync"
    );
  }
}

function pinchDistance(points: Map<number, { x: number; y: number }>) {
  const pts = [...points.values()];
  if (pts.length < 2) return 0;
  return Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
}



/** 저장된 파이프라인에서 pet_id 만 읽는다 (Provider 에 넘길 값). */
function readPipelinePetId(): string | null {
  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredPipeline;
    return parsed.content_id ? getEternalBeamPetId(parsed.content_id) : null;
  } catch {
    return null;
  }
}

/** 저장된 파이프라인 전체를 읽는다. 실패하면 null — 레거시 그대로다. */
function readStoredPipeline(): StoredPipeline | null {
  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
    return raw ? (JSON.parse(raw) as StoredPipeline) : null;
  } catch {
    return null;
  }
}

/**
 * 조정 화면도 **같은 런타임**을 돌린다(같은 훅·스케줄러·플레이어). 그래서 적격성
 * 규칙도 같아야 한다 — 여기만 빠지면 만료된 사용자가 조정 화면에서는 프리미엄
 * 행동을 계속 보게 된다.
 */
export function PreviewScreen(props: PreviewScreenProps) {
  // 라이브러리 경로에서는 발행 메타데이터의 pet_id 가 정본이다 — sessionStorage
  // 파생값(getEternalBeamPetId)은 이전 업로드 세션의 결속을 그대로 쓸 수 있다.
  const [petId] = useState(() =>
    props.isLibraryFlow && props.libraryPublication
      ? props.libraryPublication.petId
      : readPipelinePetId()
  );
  return (
    <PremiumAssetsProvider petId={petId} enabled={petId != null}>
      <PreviewScreenInner {...props} />
    </PremiumAssetsProvider>
  );
}

function PreviewScreenInner({
  cutoutImage,
  isLibraryFlow = false,
  libraryPublication = null,
  originalPhoto: originalPhotoProp = null,
  selectedTheme,
  language = "ko",
  settings,
  onSettingsChange,
  deliveryMode = "device",
  onComplete,
  onBack,
  onPlayOnBeam,
  onOpenMembership,
  onChangeMotion,
}: PreviewScreenProps) {
  const p = memorialT(language).preview;
  const [displaySettings, setDisplaySettings] = useState(settings);
  const [hasGestured, setHasGestured] = useState(false);
  // 지연 초기화 — 첫 렌더부터 정확한 값을 그린다. effect 로 마운트 뒤에 채우면
  // 그 사이(첫 페인트) 라이브러리 자산이 없는 것처럼 그려졌다가 바뀐다.
  const [pipeline, setPipeline] = useState<StoredPipeline | null>(() =>
    applyLibraryOverride(readStoredPipeline(), isLibraryFlow, libraryPublication)
  );
  // 지연 초기화 — 재개 대상(진행 중인 실행)이 있으면 첫 페인트부터 진행 화면을
  // 보여준다. 여기서 false 로 시작하면 재개 effect 가 돌기 전 한 프레임 동안
  // 조정 화면이 잠깐 비쳤다 사라진다.
  const [generating, setGenerating] = useState(() => {
    if (isLibraryFlow || !phase7GenerationEnabled()) return false;
    try {
      const meta = getPendingCutoutMeta();
      if (!meta || hasRealIdleVideo(readStoredPipeline())) return false;
      return Boolean(readActiveGeneration(meta.contentId));
    } catch {
      return false;
    }
  });
  const [genError, setGenError] = useState<string | null>(null);
  const [genErrorRecoverable, setGenErrorRecoverable] = useState(false);
  const [runState, setRunState] = useState<GenerationRun | null>(null);
  const [userTestFeedback, setUserTestFeedback] = useState<
    "idle" | "submitting" | "sent" | "error"
  >("idle");
  useEffect(() => {
    setUserTestFeedback("idle");
  }, [runState?.run_id]);
  /** 확인/재개가 실제로 generation-run 제출을 시도했는가 — 순수 클라이언트
   *  검증 오류(사진 없음 등)는 여기 걸리지 않는다. 새 폴 진행 화면의 전면
   *  오류 카드는 **제출이 실제로 일어난 뒤**에만 뜬다. */
  const runAttemptedRef = useRef(false);
  const { seconds: elapsedSec } = useProcessingClock(generating);

  const previewThemeId = resolveSelectedThemeId(selectedTheme);
  const currentTheme =
    (previewThemeId != null ? getMemorialTheme(previewThemeId) : undefined) ??
    getMemorialTheme(DEFAULT_THEME_ID)!;
  const previewBgVideo = getEffectiveBgVideo(currentTheme);
  /**
   * "원본 사진 그대로" 갈래.
   *
   * 이 갈래에서는 **피사체 레이어를 그리지 않는다.** 원본 사진에 아이가 이미
   * 들어 있으므로 누끼를 위에 얹으면 같은 아이가 두 번 보인다(살짝 어긋난 채로).
   * 배치 조절도 의미가 없다 — 원래 구도가 곧 승인된 구도다.
   */
  const isOriginalPhotoTheme = currentTheme.themeKey === ORIGINAL_PHOTO_THEME_KEY;
  /**
   * 원본 갈래의 배경 = **부모가 내려 준 한 장.**
   *
   * 예전에는 여기서 직접 localStorage 를 읽었다. 업로드 화면 경로가 그 값을
   * 저장하지 않았기 때문에, 화면에는 방금 올린 사진이 보이는데 배경은 지난번
   * 사진이거나 비어 있었다. 저장 값은 이제 **폴백**일 뿐이다.
   */
  const originalPhoto = isOriginalPhotoTheme
    ? originalPhotoProp || readOriginalPhoto()
    : null;
  /** 원본을 골랐는데 보여 줄 사진이 없다 — 검은 판 대신 오류를 보여 준다. */
  const originalMissing = isOriginalPhotoTheme && !originalPhoto;
  const settingsRef = useRef(settings);
  const displaySettingsRef = useRef(settings);
  const subjectLayerRef = useRef<HTMLDivElement>(null);
  const gestureRef = useRef({
    pointers: new Map<number, { x: number; y: number }>(),
    startPoints: new Map<number, { x: number; y: number }>(),
    anchor: { scale: 1, posX: 0, posY: 0 },
    pinchStartDistance: null as number | null,
  });
  settingsRef.current = settings;
  displaySettingsRef.current = displaySettings;

  // 제스처 중에는 React 렌더를 거치지 않고 style 을 직접 쓴다. 접지 보정(subjectShiftPct)이
  // 빠지면 드래그를 시작하는 순간 피사체가 위로 튀므로 여기서도 반드시 함께 적용한다.
  const subjectShiftPctRef = useRef(0);

  const applySubjectTransform = useCallback((s: { scale: number; posX: number; posY: number }) => {
    const el = subjectLayerRef.current;
    if (!el) return;
    el.style.transform = subjectTransform({ ...s, shiftPct: subjectShiftPctRef.current });
  }, []);

  useEffect(() => {
    setDisplaySettings(settings);
    applySubjectTransform(settings);
  }, [settings, applySubjectTransform]);

  useEffect(() => {
    if (previewThemeId != null) {
      assertPreviewTheme(selectedTheme, previewThemeId);
    }
  }, [selectedTheme, previewThemeId]);

  const cutoutDisplay =
    cutoutImage ||
    pipeline?.cutout_display_url ||
    pipeline?.dog_only_nobg_url ||
    null;
  /** My Library 경로 — 정적 누끼 없이 발행된 영상만으로 들어온다.
   *  cutout-필요 가드는 이 경로에서만 건너뛴다. */
  const isLibrarySource = pipeline?.generation_source === "library";
  // 접지 그림자는 피사체가 커질수록 살짝 진해지되 과하지 않게 상한을 둔다.
  const contactShadowOpacity = Math.min(0.5, 0.28 * displaySettings.scale);

  // 테마 접지선 + 클립 실측 발 여백 → 세로 보정. 최종 재생 화면
  // (memorial-device-play-screen)이 **같은 훅**을 쓴다 — 조정 화면에서 맞춘
  // 위치가 그대로 재현되어야 하므로 계산이 갈라지면 안 된다.
  const { floorY, setFeetMargin, subjectShiftPct } = usePetGrounding(
    currentTheme,
    pipeline?.idle_video_url
  );
  subjectShiftPctRef.current = subjectShiftPct;

  // 확인 전에는 실제 생성 결과가 없다 — 데모 mp4 로 채우지 않고 정적 누끼만 보여준다.
  const hasIdle = hasRealIdleVideo(pipeline);
  /**
   * 지금 화면에 나가는 자산이 **배경을 이미 담고 있는가** (Phase 25).
   *
   * `hasIdle` 을 함께 보는 이유: 영상이 없으면 나가는 것은 정적 누끼이고,
   * 누끼는 언제나 레거시 배치(테마 배경 + 접지 변환)가 맞다. 저장된 플래그만
   * 보고 판단하면 영상이 아직 없는 동안 배경이 사라진다.
   */
  const bakedAsset = {
    backgroundBaked: hasIdle && pipeline?.background_baked === true,
  };
  // 명시 전달 포맷 (Phase 7F) — 게이트 이유는 bakedAsset 과 같다.
  const breathingDeliveryFormat =
    hasIdle && resolveDeliveryFormat(pipeline) === "packed_alpha"
      ? "packed_alpha"
      : null;
  useEffect(() => {
    setPipeline(applyLibraryOverride(readStoredPipeline(), isLibraryFlow, libraryPublication));
  }, [cutoutImage, isLibraryFlow, libraryPublication]);

  // ── 발행 BREATHING 하이드레이션 (Phase 7F) ──────────────────────────────
  // 이 화면이 발행 뒤의 재생 화면이다(결제 복귀·새로고침으로 다시 들어온다).
  // 서버 발행 포인터(pets.breathing_*)가 있으면 새 서명 URL + 명시 포맷으로
  // 갱신한다 — 저장된 서명이 만료됐어도 재생이 살아난다. 실패/미발행이면
  // null 이고 저장값 그대로다. 조회(GET)만 한다 — 생성도, 기기 송출도 없다.
  useEffect(() => {
    if (isLibraryFlow || !hasIdle) return;
    let cancelled = false;
    void hydrateStoredPipeline().then((hydrated) => {
      if (!cancelled && hydrated) setPipeline(hydrated as StoredPipeline);
    });
    return () => {
      cancelled = true;
    };
    // 마운트 시 1회 — 발행 직후(finalizeOutcome)는 이미 새 URL 을 들고 있다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // BREATHING 생성과 레지스트리 등록은 별개다. READY 결과가 있으면 새 생성이든
  // 복원된 파이프라인이든 등록을 보장한다. 인증이 아직 복원되지 않았으면 안전하게
  // 대기하고, Supabase 세션이 돌아오는 즉시 같은 canonical petId 로 재시도한다.
  useEffect(() => {
    if (!hasIdle || !pipeline?.content_id || !pipeline.idle_video_url) return;
    // ── Phase 7G: 새 실행 산출물은 클라이언트 등록을 하지 않는다 ────────────
    // pets 포인터의 저자는 서버 발행(Phase 7A) 하나다. 여기서 register 를 쏘면
    // (a) REVIEW 개발 재생이 진짜 발행처럼 pets 에 박히고 (b) 발행된 packed
    // 포인터를 클라이언트 값으로 덮어써 새 생성 상태가 사라진다.
    if (pipeline.generation_source === "phase7-run") return;

    const registration = {
      petId: getEternalBeamPetId(pipeline.content_id) ?? `pet_${pipeline.content_id}`,
      contentId: pipeline.content_id,
      breathingUrl: pipeline.idle_video_url,
    };
    let disposed = false;

    const ensure = async () => {
      const result = await ensurePetRegistered(registration);
      if (disposed) return;
      if (result.state === "FAILED") {
        console.warn("[preview] READY pet registry ensure failed", {
          petId: registration.petId,
          code: result.code,
          status: result.status,
          message: result.message,
        });
      }
    };

    void ensure();
    const unsubscribe = onAuthStateChange((signedIn) => {
      if (signedIn) void ensure();
    });
    return () => {
      disposed = true;
      unsubscribe();
    };
  }, [hasIdle, pipeline?.content_id, pipeline?.idle_video_url, pipeline?.generation_source]);

  // 승격된 COME_CLOSER 자산을 확보한다. 없으면 **이 펫·이 테마로 1회만** 생성한다.
  //
  // 새 업로드는 새 content_id → 새 pet_id 를 만든다. 예전 업로드의 COME_CLOSER 를
  // 재사용하지 않는다(다른 사진에서 만든 클립이라 펫이 바뀌어 보인다). 대신 같은
  // 키로 새로 만든다: (user_id, pet_id, place_id, COME_CLOSER).
  //
  // 중복 제출은 서버가 최종적으로 막는다 — 여기 가드는 왕복을 줄일 뿐이다.
  const [comeCloserState, setComeCloserState] = useState<ComeCloserDiscoveryState>("idle");
  // 제스처 핸들러는 useCallback 으로 고정돼 있어 state 를 직접 읽으면 낡은 값을 본다.
  const comeCloserStateRef = useRef<ComeCloserDiscoveryState>("idle");
  comeCloserStateRef.current = comeCloserState;
  // Phase 7I.1 — 발견원. Provider 는 이 화면의 바깥 컴포넌트가 이미 세운다.
  const { assets: premiumAssetsForDiscovery } = usePremiumAssetsContext();

  useEffect(() => {
    if (!pipeline) return;
    // ⚠️ **확인(confirm) 전에는 아무것도 생성하지 않는다.**
    //
    // 예전 게이트는 `pipeline` 존재뿐이었다. 그런데 파이프라인은 누끼가 끝나면
    // ai-processing-screen 이 `idle_video_url: ""` 로 미리 써 둔다. 그래서 사용자가
    // "이 배경으로 영상 만들기" 를 누르기 **전에** 프리미엄 5종이 생성되기 시작했고,
    // 되돌아가거나 테마를 바꾸면 그 비용은 그대로 날아갔다. 게다가 그때는 BREATH 가
    // 없어 재생기가 마운트되지 않으므로, 만든 자산을 보여줄 수도 없었다.
    //
    // 이제 BREATH 가 존재할 때만 — 즉 사용자가 확인을 누른 뒤에만 — 동작한다.
    // 최초 착수는 handleConfirm 이 명시적으로 한다(이 effect 는 그 뒤 재방문 시
    // 이미 있는 자산을 집어 오는 역할).
    if (!hasIdle) return;
    // ── Phase 7I.1: 발견원이 인증 컨텍스트 하나다 ───────────────────────────
    // 예전에는 무과금 개발 엔드포인트(lookupComeCloserAsset, DEV 게이트)를 직접
    // 조회/폴링했다 — 프로덕션에서는 발견이 아예 되지 않았다. 이제 아이들 4종과
    // **같은 인증 READY 계약**(PremiumAssetsProvider → GET /premium/assets)을
    // 읽는다. 생성 중이면 Provider 가 폴링하고, READY 가 되는 순간 여기로 온다.
    // 이 effect 는 여전히 **조회만 한다** — 새 생성은 purchasePremium 뿐이다.
    const petId = getEternalBeamPetId(pipeline.content_id);
    const readyUrl = premiumAssetsForDiscovery?.readyAssets?.COME_CLOSER?.url ?? null;
    const generating = Boolean(
      premiumAssetsForDiscovery?.generating.includes("COME_CLOSER")
    );
    setComeCloserState(readyUrl ? "ready" : generating ? "generating" : "idle");
    if (readyUrl) {
      if (
        readyUrl !== pipeline.come_closer_video_url ||
        pipeline.come_closer_pet_id !== petId
      ) {
        setPipeline(mergeComeCloserIntoPipeline(pipeline, readyUrl, petId));
      }
      return;
    }
    // READY 가 아닌데 **다른 펫**의 캐시가 남아 있으면 지운다 (교차 재생 방지).
    // 같은 펫의 캐시는 남긴다 — 발견이 잠시 비어도 레거시 자산 재생을 죽이지 않는다.
    if (
      pipeline.come_closer_video_url &&
      pipeline.come_closer_pet_id &&
      pipeline.come_closer_pet_id !== petId
    ) {
      setPipeline(mergeComeCloserIntoPipeline(pipeline, null, null));
    }
    // 의존성에 테마가 **없다** — 테마 변경이 생성/조회를 유발해선 안 된다.
  }, [pipeline, hasIdle, premiumAssetsForDiscovery]);

  // ── 아이들 이벤트 자산 (4종) — **개발 빌드 전용** ─────────────────────────
  // 스윕 구현은 use-idle-event-assets 로 빠졌다. memorial-device-play-screen 이
  // **같은 훅**을 쓴다 — 이 루프는 유료 생성을 제출하므로 사본이 갈라지면 한쪽에서만
  // 중복 지출이 난다. DEV 게이트와 스윕 주기도 그 훅이 단독으로 갖는다.
  //
  // enabled 는 **실제 BREATH 자산**으로만 판정한다(hasRealIdleVideo) — 데모 폴백
  // mp4 를 근거로 켜면 확인 전에 유료 생성이 나가고 이음매 전제도 깨진다.
  const {
    urls: idleEventUrls,
    formats: idleEventFormats,
    availableIds: availableIdleEventIds,
  } = useIdleEventAssets({
    pipeline,
    enabled: hasIdle,
  });
  // 이벤트별 전달 포맷 (Phase 7I.1) — devicePlay 와 같은 배선.
  const eventDeliveryFormats = useMemo(
    () => ({
      ...idleEventFormats,
      COME_CLOSER:
        premiumAssetsForDiscovery?.readyAssets?.COME_CLOSER?.deliveryFormat ?? null,
    }),
    [idleEventFormats, premiumAssetsForDiscovery]
  );

  // ── 런타임 적격성 (재생 화면과 **같은 규칙**) ──────────────────────────────
  // 구독 entitled ∩ 자산 READY ∩ 선호 ON. 스케줄러·플레이어·런타임은 그대로 두고
  // 입력만 좁힌다. BREATHING 은 판정 밖이라 계속 돈다.
  const eligibility = useBehaviorEligibility();
  const eligibleIdleEventIds = eligibility.filterIds(availableIdleEventIds);
  const eligibleIdleEventSources = eligibility.filterSources(idleEventUrls);
  const comeCloserAllowedRef = useRef(eligibility.comeCloserAllowed);
  comeCloserAllowedRef.current = eligibility.comeCloserAllowed;

  // 콘솔에서 부르는 수동 트리거. 프로덕션 빌드에는 존재하지 않는다.
  // 이벤트별 별칭 + 범용 훅을 함께 심는다.
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const w = window as unknown as Record<string, unknown>;
    const aliases: Partial<Record<IdleEvent, string>> = {
      BLINKING: "__ebBlink",
      EAR_TWITCHING: "__ebEarTwitch",
      HEAD_TILTING: "__ebHeadTilt",
      TAIL_WAGGING: "__ebTailWag",
    };

    const fireEvent = (eventId: IdleEvent) => {
      const fire = comeCloserTriggerRef.current;
      if (!fire) {
        console.warn(`[${eventId}] 트리거 없음 — BREATH 영상이 없거나 자산 미확보`);
        return;
      }
      console.info(`[${eventId}] 수동 트리거`);
      fire(eventId);
    };

    const installed: string[] = [];
    for (const def of registeredIdleEvents()) {
      const eventId = def.id as IdleEvent;
      const name = aliases[eventId];
      if (!name) continue;
      w[name] = () => fireEvent(eventId);
      installed.push(name);
    }
    // 별칭이 없는 신규 이벤트도 바로 시험할 수 있게 범용 훅을 둔다.
    w.__ebIdleEvent = (id: string) => fireEvent(id as IdleEvent);
    console.info("[idle-event] dev 훅:", [...installed, "__ebIdleEvent(id)"].join(", "));

    return () => {
      for (const name of installed) delete w[name];
      delete w.__ebIdleEvent;
    };
  }, []);

  // ── 확인 → idle 생성 ────────────────────────────────────────────────────
  // 누끼 직후가 아니라 여기서 처음으로 유료 생성이 일어난다. 테마는 프론트
  // 전용이므로 생성 요청에 테마 정보를 싣지 않는다.
  const generatingRef = useRef(false);

  // Phase 7 실행 결과(발행/REVIEW)를 파이프라인·기기 송출·다음 화면 전환에
  // 반영한다 — 방금 끝난 확인(handleConfirm)과 새로고침 재개(아래 effect)
  // 가 **같은 코드**로 마무리되게 한다. 갈라지면 재개 경로만 기기 송출을
  // 빠뜨리는 식의 결함이 생긴다.
  const finalizeOutcome = useCallback(
    async (outcome: Phase7Outcome, resolvedPetId: string, contentId: string, displayUrl: string) => {
      const next: StoredPipeline = {
        content_id: contentId,
        cutout_display_url: pipeline?.cutout_display_url || displayUrl,
        dog_only_nobg_url: pipeline?.dog_only_nobg_url || displayUrl,
        action_video_url: pipeline?.action_video_url || "",
        come_closer_video_url: pipeline?.come_closer_video_url ?? null,
        phase1_intake: pipeline?.phase1_intake,
        scene_id: pipeline?.scene_id ?? null,
        ...phase7PipelinePatch(outcome),
      };
      try {
        sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(next));
        localStorage.setItem("eternal_beam_content_id", next.content_id);
        localStorage.setItem("eternal_beam_current_content_id", next.content_id);
        // hologram_video_id 는 기기(S23) 송출용 레거시 키다 — packed vstack 을
        // 그대로 보내면 기기에서 이중 화면이 되므로 새 경로는 채우지 않는다.
      } catch {
        /* ignore quota */
      }
      setPipeline(next);
      // outcome.run 이 정본 종료 상태(PUBLISHED)다 — 마지막 폴링 tick 의
      // onProgress 경합에 기대지 않고 진행 화면에도 곧장 반영한다.
      setRunState(outcome.run);
      const devicePetId = getEternalBeamPetId(next.content_id) ?? resolvedPetId;
      // P0 — publication finishing must never itself push to the Beam. Only
      // an explicit "Play on Beam" press (My Library's onPlayOnBeam) may send
      // theme_play/pet_asset; this path used to fire pet_asset unconditionally
      // and fire-and-forget the instant the run published, with no consent,
      // no failure surfaced, and no device_id resolution.
      if (
        devicePetId &&
        outcome.run.status === "PUBLISHED" &&
        // Fallback delivery has no published pointer to hydrate from — keep
        // the marker so a refresh re-resolves the fallback playback URL.
        outcome.run.terminal_state !== "DELIVERED_FALLBACK"
      ) {
        // Phase 9 — 발행(PUBLISHED)은 종착점이다. 재개 마커를 여기서 지우지
        // 않으면 다시 열 이유가 없는 완료된 실행이 localStorage 에 영원히
        // 남는다(REVIEW/FAILED/RECOVERY_REQUIRED 는 여전히 재개 대상이라
        // 남긴다 — 새로고침이 그 복구 화면을 다시 보여줘야 한다).
        clearActiveGeneration(next.content_id);
      }
      // 완료 체크 표시를 잠깐 보여준다 — ai-processing-screen 의 완료 지연
      // (300~500ms)과 같은 패턴.
      await new Promise((r) => setTimeout(r, 450));
      // 기기 전달(device)은 **여기 머무른다** — 진행 화면이 내려가면 같은
      // Composer 가 발행 결과를 재생하고 Play on Web / Play on Beam 을 연다.
      // 예전에는 여기서 onComplete → 직접 Pi 송출 화면(devicePlay)으로 넘어가
      // 마운트만으로 LAN 탐색과 직접 Pi POST 가 나갔다. 실물(shipping)만 다음
      // 화면(배송지)이 있다.
      if (deliveryMode === "shipping") onComplete();
    },
    [pipeline, onComplete, deliveryMode]
  );

  // ── 새로고침 안전 재개 (Phase 7) ────────────────────────────────────────
  // 마운트 시 한 번, 확인이 실제로 눌린 적 있는 content_id 인지(durable
  // 마커) 확인한다. 없으면(아직 확인을 안 누른 사용자) 아무 일도 하지
  // 않는다 — 새 사용자/활성 실행 없음 동작은 그대로다. 있으면 새 실행을
  // 만들지 않고 기존 실행 상태를 다시 조회해 이어서 폴링하거나, 이미 끝난
  // 상태(PUBLISHED/REVIEW/FAILED/RECOVERY_REQUIRED)를 그대로 복원한다.
  useEffect(() => {
    if (!phase7GenerationEnabled() || isLibraryFlow) return;
    if (generatingRef.current || hasIdle) return;
    const meta = getPendingCutoutMeta();
    if (!meta) return;
    const record = readActiveGeneration(meta.contentId);
    if (!record) return; // 확인이 눌린 적 없다 — 재개할 것이 없다.
    const resolvedPetId = pipeline?.phase1_intake?.pet_id ?? getEternalBeamPetId(meta.contentId);
    if (!resolvedPetId) return;

    let cancelled = false;
    generatingRef.current = true;
    runAttemptedRef.current = true;
    setGenError(null);
    setGenErrorRecoverable(false);
    setGenerating(true);
    (async () => {
      try {
        const outcome = await resumePhase7Generation({
          petId: resolvedPetId,
          contentId: meta.contentId,
          runId: record.runId,
          poll: {
            onProgress: (run) => {
              if (!cancelled) setRunState(run);
            },
          },
        });
        if (cancelled) return;
        await finalizeOutcome(outcome, resolvedPetId, meta.contentId, meta.displayUrl);
      } catch (e) {
        if (cancelled) return;
        const message = e instanceof Error && e.message ? e.message : String(e);
        console.warn("[preview] Phase 7 generation resume failed", e);
        setGenError(message);
        setGenErrorRecoverable(isRecoverableErrorCode((e as { code?: string } | undefined)?.code));
      } finally {
        if (!cancelled) {
          generatingRef.current = false;
          setGenerating(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // 마운트 시 1회만 — content_id/pipeline 은 이 재개 판단의 입력일 뿐,
    // 매 렌더 재실행 대상이 아니다(재개 중 pipeline 이 갱신되며 자기 자신을
    // 다시 트리거하는 루프를 막는다).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleConfirm = useCallback(async () => {
    if (hasIdle) {
      onComplete();
      return;
    }
    // 원본을 골랐는데 그 사진이 없다. **제출하지 않는다** — 배경 없는(검은)
    // 그림으로 유료 생성이 돌면 고객은 결제 뒤에야 알게 된다.
    if (originalMissing) {
      setGenError(
        sceneErrorMessage("ORIGINAL_PHOTO_MISSING", language === "en" ? "en" : "ko")
      );
      return;
    }
    if (generatingRef.current) return; // 더블탭으로 두 번 생성되는 것 방지
    generatingRef.current = true;
    setGenError(null);
    setGenErrorRecoverable(false);
    setGenerating(true);

    // ═══ Phase 7G — 새 생성 시스템 (기본 경로) ═══════════════════════════
    // 확인은 이제 generation-run 하나를 만들고 폴링한다. Phase 2–6·QA·포장은
    // 전부 서버 워커가 수행한다. 테마는 생성에 실리지 않는다 — 장면 굽기도
    // 없다. 아래 레거시 본문은 보존되지만(명시적 회귀 스위치 전용) 새 펫
    // 흐름에서는 **절대 실행되지 않고**, 실패해도 그쪽으로 떨어지지 않는다.
    if (phase7GenerationEnabled()) {
      try {
        const meta = getPendingCutoutMeta();
        if (!meta) throw new Error(p.generateMissingCutout);
        const petId =
          pipeline?.phase1_intake?.pet_id ?? getEternalBeamPetId(meta.contentId);
        if (!petId) throw new Error(p.generateMissingCutout);

        runAttemptedRef.current = true;
        const outcome = await runPhase7Generation({
          petId,
          contentId: meta.contentId,
          poll: { onProgress: (run) => setRunState(run) },
        });
        await finalizeOutcome(outcome, petId, meta.contentId, meta.displayUrl);
      } catch (e) {
        const message =
          e instanceof Error && e.message ? e.message : String(e);
        console.warn("[preview] Phase 7 generation failed — 레거시 폴백 없음", e);
        setGenError(message);
        setGenErrorRecoverable(isRecoverableErrorCode((e as { code?: string } | undefined)?.code));
      } finally {
        generatingRef.current = false;
        setGenerating(false);
      }
      return;
    }
    // ═══ 레거시 경로 (VITE_LEGACY_GENERATION=1 전용) ═════════════════════
    try {
      const meta = getPendingCutoutMeta();
      const cutFile = await rehydrateCutoutFile();
      if (!meta || !cutFile) {
        throw new Error(p.generateMissingCutout);
      }

      // ── 승인된 장면을 굽는다 ─────────────────────────────────────────
      // 여기가 "고객이 승인한 그림"이 존재하는 유일한 순간이다. 지금 굽지 않으면
      // 프로바이더는 배경을 영영 보지 못한다.
      //
      // ⚠️ **배경을 골랐는데 굽지 못하면 멈춘다** (Phase 20). 예전에는 검정 판으로
      // 조용히 떨어져 그대로 생성했고, 고객은 고른 적 없는 배경의 영상을 받은 채
      // 돈만 나갔다. 장면 실패는 제출 **전**이므로 공짜로 복구할 수 있다 —
      // 그 기회를 없애고 유료 생성으로 넘어가면 안 된다.
      //
      // 배경 선택 단계 자체가 없는 레거시 흐름(previewThemeId == null)에서만
      // 예전 누끼 전용 경로가 허용된다.
      const userId = getEternalBeamUserId();
      const backgroundWasChosen = previewThemeId != null;
      let scene: CanonicalScene | null = null;
      try {
        scene = await buildCanonicalScene({
          userId,
          contentId: meta.contentId,
          petCutoutUrl: meta.displayUrl,
          placement: {
            scale: displaySettings.scale,
            posX: displaySettings.posX,
            posY: displaySettings.posY,
            // 미리보기가 **실제로 적용한** 보정값. 다시 계산하지 않는다.
            shiftPct: subjectShiftPct,
          },
          floorY,
          // 화면이 그린 그림과 **같은 값**을 넘긴다. 여기서 다시 저장소를 읽으면
          // 미리보기와 생성이 갈라질 수 있다 — 그것이 이번에 고친 결함이다.
          background: resolveSceneBackground(currentTheme, originalPhoto),
          // 피사체 레이어는 inset-0 이라 그 높이가 곧 프레임 높이다
          // (pet-grounding.ts 의 % 기준과 같다).
          previewFrameHeight: subjectLayerRef.current?.clientHeight,
          requireBackground: backgroundWasChosen,
        });
      } catch (e) {
        if (backgroundWasChosen) {
          // **제출하지 않는다.** 과금되지 않았음을 문구로도 분명히 한다.
          const code = isSceneError(e) ? e.code : "SCENE_PREPARATION_FAILED";
          console.warn("[preview] 장면 준비 실패 — 생성하지 않는다", code, e);
          setGenError(sceneErrorMessage(code, language === "en" ? "en" : "ko"));
          return;
        }
        console.warn("[preview] 레거시 흐름 — 장면 없이 생성", e);
      }

      const pet = await requestIdleGeneration({
        cutFile,
        contentId: meta.contentId,
        // idle 과 COME_CLOSER 가 같은 신원 아래 모이게 한다. 넘기지 않으면
        // 백엔드가 'anonymous' 로 저장해 이후 조회가 영영 어긋난다.
        userId,
        scene,
      });

      // ── 보낸 장면이 쓰였는가 (Phase 26) ──────────────────────────────────
      // 서버는 이제 장면을 준비하지 못하면 SCENE_UNAVAILABLE 로 **제출 전에**
      // 거절한다(위 catch 에서 잡힌다). 그래도 여기서 한 번 더 확인하는 이유는,
      // 이 화면이 구버전 서버를 상대할 수 있기 때문이다 — 그쪽은 200 과 함께
      // background_baked=false 를 돌려준다.
      //
      // 그 false 를 조용히 기록하면 예전 결함이 그대로 살아난다: 고객은 고른 적
      // 없는 배경의 영상을 받고, 화면은 아무 말도 하지 않는다.
      if (scene && pet.background_baked !== true) {
        console.warn(
          "[preview] 장면을 보냈는데 서버가 쓰지 않았다 — 저장하지 않는다",
          scene.sceneId
        );
        setGenError(serverGenerationMessage("SCENE_UNAVAILABLE", language === "en" ? "en" : "ko") ?? "");
        return;
      }

      const next: StoredPipeline = {
        content_id: pet.content_id || meta.contentId,
        cutout_display_url: pipeline?.cutout_display_url || meta.displayUrl,
        dog_only_nobg_url: pet.dog_only_nobg_url || meta.displayUrl,
        idle_video_url: pet.idle_video_url || "",
        action_video_url: pet.action_video_url || "",
        // 재생 쪽이 배경을 다시 합성하지 않도록 하는 신호. 서버 응답을 정본으로
        // 삼는다 — 위 검사를 통과했으므로 장면을 보냈다면 반드시 true 다.
        background_baked: pet.background_baked === true,
        scene_id: (pet.scene_id as string | null) ?? scene?.sceneId ?? null,
      };

      try {
        sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(next));
        localStorage.setItem("eternal_beam_content_id", next.content_id);
        localStorage.setItem("eternal_beam_current_content_id", next.content_id);
        if (next.idle_video_url) {
          localStorage.setItem("eternal_beam_hologram_video_id", next.idle_video_url);
          localStorage.setItem("eternal_beam_current_video_id", next.idle_video_url);
        }
      } catch {
        /* ignore quota */
      }
      setPipeline(next);

      if (next.idle_video_url) {
        schedulePetReadyToDevice({
          contentId: next.content_id,
          idleUrl: next.idle_video_url,
          cutoutUrl: next.cutout_display_url,
        });

        // ── 프리미엄 착수 없음 — 확인은 결제가 아니다 ───────────────────────
        //
        // 예전에는 여기서 COME_CLOSER + 첫 아이들 이벤트를 자동 제출했다. 무과금
        // 개발 엔드포인트에서는 "미리 만들어 두기" 였지만, 확정된 사업 모델에서는
        // 그게 곧 **사용자 동의 없는 결제**다(아이들 번들 1크레딧, 액션 1크레딧).
        //
        // 이제 생성은 명시적 구매에서만 일어난다:
        //   lib/premium-assets.ts → purchasePremium(KIND_IDLE_BUNDLE | actionKind(...))
        // 이 화면은 확인 후 발견(GET)만 하고, 없는 자산은 없는 채로 둔다 —
        // 스케줄러는 READY 인 것만 고르므로 BREATHING 으로 조용히 남는다.
      }

      onComplete();
    } catch (e) {
      // 장면 실패와 서버의 **제출 전** 거절(멱등성 불가·이미 진행 중)은
      // 프로바이더 실패가 아니다. 같은 문구로 뭉뚱그리면 고객이 "다시 생성"을
      // 눌러 유료 제출을 반복하게 된다.
      const lang = language === "en" ? "en" : "ko";
      if (isSceneError(e)) {
        setGenError(sceneErrorMessage(e.code, lang));
      } else {
        const code =
          (e as { code?: string })?.code ??
          ((e as { detail?: { code?: string } })?.detail?.code || "");
        const preSubmission = code ? serverGenerationMessage(code, lang) : null;
        setGenError(
          preSubmission ?? (e instanceof Error ? e.message : String(e))
        );
      }
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
    // originalMissing/originalPhoto/language 는 **반드시** 여기 있어야 한다.
    // 빠지면 콜백이 예전 값을 붙잡고 있다가, 사진을 다시 올린 뒤에도 "원본
    // 없음"으로 막거나 지난번 사진으로 생성한다.
  }, [
    hasIdle,
    onComplete,
    finalizeOutcome,
    p.generateMissingCutout,
    pipeline?.cutout_display_url,
    originalMissing,
    originalPhoto,
    language,
  ]);

  const handleReset = useCallback(() => {
    const reset = { scale: 1, posX: 0, posY: 0 };
    setDisplaySettings(reset);
    applySubjectTransform(reset);
    onSettingsChange(reset);
  }, [applySubjectTransform, onSettingsChange]);

  // ── "Play on Web" — local restart only, never a device command ──────────
  // The playback component tree exposes no imperative restart API. Remounting
  // via a bumped key is the only real way to replay from frame 0 — same
  // technique already used for ThemeBackgroundVideo's `key` below.
  const [replayKey, setReplayKey] = useState(0);
  const handlePlayOnWeb = useCallback(() => {
    setReplayKey((k) => k + 1);
  }, []);

  // ── Library 갈래 — "Beam으로 보내기" ─────────────────────────────────────
  // 상태는 여기서만 소유한다. 예전에는 MyLibraryScreen 이 두 sendDeviceCommand
  // 호출 결과를 확인하지 않고 무조건 "Sent to Beam" 을 띄웠다 — 하나 또는 둘
  // 다 실패해도 성공 배지가 떴다. onPlayOnBeam 이 돌려주는 ok 를 반드시 확인한다.
  const [beamStatus, setBeamStatus] = useState<"idle" | "sending" | "sent" | "acked" | "error">("idle");
  const [beamErrorCategory, setBeamErrorCategory] = useState<DeviceCommandFailureCategory | null>(null);
  const ackPollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (ackPollRef.current) clearTimeout(ackPollRef.current);
    };
  }, []);

  // "Sent" 는 백엔드가 명령을 받았다는 뜻일 뿐이다 — 실제로 Beam이 받았는지는
  // last_ack 을 짧게(최대 3회, 2.5s 간격) 확인해야 안다. 못 확인해도 실패로
  // 바꾸지 않는다 — 명령은 이미 성공적으로 큐에 들어갔다.
  const pollForAck = useCallback((commandId: string, attemptsLeft: number) => {
    if (attemptsLeft <= 0) return;
    const deviceId = resolvePairedDeviceId();
    if (!deviceId) return;
    ackPollRef.current = setTimeout(() => {
      void getDeviceConnectionState(deviceId).then((result) => {
        if (result.ok && result.state.last_ack === commandId) {
          setBeamStatus("acked");
          return;
        }
        pollForAck(commandId, attemptsLeft - 1);
      });
    }, 2500);
  }, []);

  const handlePlayOnBeam = useCallback(async () => {
    if (!onPlayOnBeam || beamStatus === "sending") return;
    setBeamStatus("sending");
    setBeamErrorCategory(null);
    if (ackPollRef.current) clearTimeout(ackPollRef.current);
    try {
      const result = await onPlayOnBeam();
      if (result.ok) {
        setBeamStatus("sent");
        if (result.commandId) pollForAck(result.commandId, 3);
      } else {
        setBeamStatus("error");
        setBeamErrorCategory(classifyDeviceCommandFailure({ reason: result.reason ?? "", status: result.status }));
      }
    } catch {
      setBeamStatus("error");
      setBeamErrorCategory("network");
    }
  }, [onPlayOnBeam, beamStatus, pollForAck]);
  const beamErrorMessage =
    beamErrorCategory === "auth"
      ? p.beamErrorAuth
      : beamErrorCategory === "device_unavailable"
        ? p.beamErrorUnavailable
        : beamErrorCategory === "rejected"
          ? p.beamErrorRejected
          : beamErrorCategory === "network"
            ? p.beamErrorNetwork
            : p.beamError;
  // 별도 리셋 effect 는 필요 없다 — 이 화면은 step === "preview" 일 때만
  // 존재한다(부모의 조건부 렌더). 테마를 바꾸러 나갔다 다시 들어오면
  // 컴포넌트가 통째로 새로 마운트되어 beamStatus 는 이미 "idle" 이다.

  const clampScale = (value: number) =>
    Math.round(Math.min(2, Math.max(0.5, value)) * 100) / 100;

  const clampPos = (value: number) =>
    Math.round(Math.min(100, Math.max(-100, value)) * 10) / 10;

  const commitSettings = useCallback(
    (next: { scale: number; posX: number; posY: number }) => {
      setDisplaySettings(next);
      applySubjectTransform(next);
      onSettingsChange(next);
    },
    [applySubjectTransform, onSettingsChange]
  );

  const previewLiveSettings = useCallback(
    (partial: Partial<{ scale: number; posX: number; posY: number }>) => {
      const next = { ...displaySettingsRef.current, ...partial };
      displaySettingsRef.current = next;
      setDisplaySettings(next);
      applySubjectTransform(next);
    },
    [applySubjectTransform]
  );

  const finishSliderDrag = useCallback(() => {
    onSettingsChange(displaySettingsRef.current);
  }, [onSettingsChange]);

  const reanchorGesture = useCallback(() => {
    const g = gestureRef.current;
    g.anchor = { ...displaySettingsRef.current };
    g.startPoints = new Map(g.pointers);
    g.pinchStartDistance = g.pointers.size >= 2 ? pinchDistance(g.pointers) : null;
  }, []);

  const applyGestureFrame = useCallback(() => {
    const g = gestureRef.current;
    const count = g.pointers.size;
    if (count === 0) return;

    if (count >= 2 && g.pinchStartDistance && g.pinchStartDistance > 0) {
      const ratio = pinchDistance(g.pointers) / g.pinchStartDistance;
      previewLiveSettings({
        scale: clampScale(g.anchor.scale * ratio),
      });
      return;
    }

    if (count === 1) {
      const [id, point] = [...g.pointers.entries()][0];
      const start = g.startPoints.get(id);
      if (!start) return;
      previewLiveSettings({
        posX: clampPos(g.anchor.posX + (point.x - start.x)),
        posY: clampPos(g.anchor.posY + (point.y - start.y)),
      });
    }
  }, [previewLiveSettings]);

  // ── COME_CLOSER 더블탭 ────────────────────────────────────────────────────
  // 별도 onDoubleClick 리스너를 붙이지 않는다 — 기존 드래그/핀치 핸들러가 우선권을
  // 가져야 하기 때문이다. 포인터가 거의 움직이지 않았고(탭), 두 번째 탭이
  // DOUBLE_TAP_MS 안에 들어왔을 때만 액션으로 인정한다.
  const comeCloserTriggerRef = useRef<PetRuntimeTrigger | null>(null);

  // ── 자발적 아이들 스케줄러 ─────────────────────────────────────────────────
  // 수동 트리거와 **같은 진입점**(comeCloserTriggerRef)을 쓴다. 스케줄러는
  // "무엇을 언제" 만 정하고, 재생·이음매·복귀는 전부 기존 런타임이 담당한다.
  //
  // 후보 목록(availableIdleEventIds)은 useIdleEventAssets 가 READY URL 에서 만든다 —
  // 자산이 하나도 없으면 비어 있고, 그때는 아무 일도 일어나지 않는다(BREATHING 유지).
  const { onPlaybackStateChange } = useIdleEventScheduler({
    enabled: eligibleIdleEventIds.length > 0,
    availableIds: eligibleIdleEventIds,
    triggerRef: comeCloserTriggerRef,
  });

  const lastTapRef = useRef<TapPoint | null>(null);

  const handlePreviewPointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!cutoutDisplay) return;
      e.preventDefault();
      setHasGestured(true);
      const g = gestureRef.current;
      g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      g.startPoints.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (g.pointers.size === 1) {
        reanchorGesture();
      } else if (g.pointers.size === 2) {
        reanchorGesture();
      }
      e.currentTarget.setPointerCapture(e.pointerId);
    },
    [cutoutDisplay, reanchorGesture]
  );

  const handlePreviewPointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const g = gestureRef.current;
      if (!g.pointers.has(e.pointerId)) return;
      if (!e.currentTarget.hasPointerCapture(e.pointerId)) return;
      e.preventDefault();
      g.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      applyGestureFrame();
    },
    [applyGestureFrame]
  );

  const handlePreviewPointerEnd = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const g = gestureRef.current;
      if (!g.pointers.has(e.pointerId)) return;
      try {
        if (e.currentTarget.hasPointerCapture(e.pointerId)) {
          e.currentTarget.releasePointerCapture(e.pointerId);
        }
      } catch {
        /* ignore */
      }
      const start = g.startPoints.get(e.pointerId);
      g.pointers.delete(e.pointerId);
      g.startPoints.delete(e.pointerId);
      if (g.pointers.size === 0) {
        g.pinchStartDistance = null;
        finishSliderDrag();
        // 드래그가 아니라 '탭'이었을 때만 더블탭 판정에 넣는다.
        const r = recognizeTap(
          start,
          { t: Date.now(), x: e.clientX, y: e.clientY },
          lastTapRef.current,
        );
        if (r.kind === "double") {
          lastTapRef.current = null;
          const fire = comeCloserTriggerRef.current;
          if (import.meta.env.DEV) {
            // 트리거가 없을 때 "왜 없는지"를 상태로 말해 준다 — 조용히 아무 일도
            // 일어나지 않으면 고장과 구분되지 않는다.
            console.info(
              fire
                ? '[COME_CLOSER] 더블탭 인식 → trigger("COME_CLOSER")'
                : `[COME_CLOSER] 더블탭 인식했지만 액션 없음 (state=${comeCloserStateRef.current}). ` +
                  (comeCloserStateRef.current === "generating"
                    ? "생성 중이다 — 완료되면 자동으로 재생 가능해진다."
                    : "BREATH 영상이 없거나(정적 누끼) 자산이 아직 없다. " +
                      "생성은 Behavior Library 의 구매로만 시작된다."),
            );
          }
          // 더블탭 ∩ 구독 ∩ READY ∩ ON — 자발적 경로와 같은 규칙.
          if (comeCloserAllowedRef.current) fire?.("COME_CLOSER");
        } else if (r.kind === "first") {
          lastTapRef.current = r.tap;
        }
        return;
      }
      reanchorGesture();
    },
    [finishSliderDrag, reanchorGesture]
  );

  const handlePreviewWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const delta = -e.deltaY * 0.0025;
      const next = {
        ...displaySettingsRef.current,
        scale: clampScale(displaySettingsRef.current.scale + delta),
      };
      commitSettings(next);
    },
    [commitSettings]
  );

  const themeLabel = language === "en" ? currentTheme.name : currentTheme.nameKo;
  // 발행된 재생 레일 — Library 갈래는 언제나, Create 갈래는 기기 전달(device)로
  // 발행이 끝난 뒤. 실물(shipping)은 배송지로 이어지는 확인 CTA 를 그대로 둔다.
  const showPublishedRail = isLibraryFlow || (hasIdle && deliveryMode === "device");
  const userTestEnrolled = Boolean(
    (
      runState?.provider_state?._business_qa_cutover as
        | { enrolled?: boolean }
        | undefined
    )?.enrolled
  );
  const handleUserTestFeedback = useCallback(
    async (accepted: boolean, complaint?: MotionQAComplaint) => {
      if (!runState?.run_id || userTestFeedback === "submitting") return;
      setUserTestFeedback("submitting");
      try {
        await submitBusinessQAFeedback(runState.run_id, {
          accepted,
          complaints: complaint ? [complaint] : [],
        });
        setUserTestFeedback("sent");
      } catch {
        setUserTestFeedback("error");
      }
    },
    [runState?.run_id, userTestFeedback]
  );
  // Only BREATHING exists once a Create-flow pet is READY — the device contract
  // supports nothing else yet, so this is real data, not a placeholder.
  const motionLabel = isLibraryFlow ? libraryPublication?.motionId ?? null : hasIdle ? "BREATHING" : null;
  // No pet name or breed field exists anywhere in the frontend-reachable data
  // (StoredPipeline/LibraryPublication/my-library-api) — only a real thumbnail
  // does (cutoutDisplay is already "" in Library flow, since it carries no
  // static cutout). Do not fabricate a name/breed; show the avatar alone.
  const petAvatarUrl = cutoutDisplay || null;

  // ── Phase 4 — 진행 화면 ────────────────────────────────────────────────
  // Phase 7 제출/재개가 도는 동안, 그리고 그것이 **실제로 제출을 시도한 뒤**
  // ── Retry (Phase 7) ──────────────────────────────────────────────────────
  // 실패 화면의 "다시 시도" 는 확인(handleConfirm)을 다시 누르는 것이 **아니다**.
  // 확인은 같은 idempotency_key 로 같은 실행에 합류하므로, FAILED / CANCELLED
  // 실행은 폴링 없이 곧바로 같은 오류로 끝났고 서버에서는 아무것도 바뀌지
  // 않았다. 이제는 알고 있는 run_id 로 명시적 retry API 를 불러 그 실행을
  // QUEUED 로 되돌린다. run_id 를 모르면(실행이 만들어지기 전의 네트워크
  // 오류 등) 확인 경로로 떨어져 새로 시작한다.
  const handleRetry = useCallback(async () => {
    if (hasIdle || generatingRef.current) return;
    const meta = getPendingCutoutMeta();
    const record = meta ? readActiveGeneration(meta.contentId) : null;
    const runId = runState?.run_id ?? record?.runId ?? null;
    const petId = meta
      ? (pipeline?.phase1_intake?.pet_id ?? getEternalBeamPetId(meta.contentId))
      : null;
    if (!meta || !runId || !petId) {
      await handleConfirm();
      return;
    }
    generatingRef.current = true;
    runAttemptedRef.current = true;
    setGenError(null);
    setGenErrorRecoverable(false);
    setGenerating(true);
    try {
      const outcome = await retryPhase7Generation({
        runId,
        petId,
        contentId: meta.contentId,
        poll: { onProgress: (run) => setRunState(run) },
      });
      await finalizeOutcome(outcome, petId, meta.contentId, meta.displayUrl);
    } catch (e) {
      const message = e instanceof Error && e.message ? e.message : String(e);
      console.warn("[preview] Phase 7 generation retry failed", e);
      setGenError(message);
      setGenErrorRecoverable(isRecoverableErrorCode((e as { code?: string } | undefined)?.code));
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
  }, [hasIdle, runState, pipeline, handleConfirm, finalizeOutcome]);

  // 실패했을 때는 조정 UI 대신 이 화면을 보여준다. 순수 클라이언트 검증
  // 오류(사진 없음 등, runAttemptedRef 가 아직 false)는 여기 해당하지 않고
  // 기존 조정 화면의 인라인 오류 문구로 남는다.
  const showGenerationProgress = generating && phase7GenerationEnabled();
  const showGenerationError =
    !generating &&
    phase7GenerationEnabled() &&
    runAttemptedRef.current &&
    !hasIdle &&
    Boolean(genError);

  if (showGenerationProgress || showGenerationError) {
    const view = showGenerationProgress
      ? deriveGenerationProgress(runState)
      : ({ kind: "error", recoverable: genErrorRecoverable, message: genError } as const);
    return (
      <GenerationProgressScreen
        view={view}
        elapsedSec={elapsedSec}
        language={language}
        previewImageUrl={cutoutDisplay}
        onBack={onBack}
        onRetry={showGenerationError ? handleRetry : undefined}
      />
    );
  }

  return (
    <div className="preview-composer h-full flex flex-col min-h-0 overflow-hidden">
      {/* Header — Back (left) / title (+ subtitle) / Change Theme (right, desktop only).
          "Change Theme" reuses the same onBack the screen already had — for both
          flows, onBack already returns to Theme Selection with selectedPet/
          selectedMotion (Library) or the pipeline (Create) preserved. Mobile hides
          this trailing button and surfaces an equivalent theme row in the panel
          instead (there isn't room for three header elements on a phone width). */}
      <header className="eb-screen-header preview-composer__header">
        <div className="eb-screen-header__leading">
          <BackButton onClick={onBack} label={memorialT(language).common.back} />
        </div>
        <div className="preview-composer__heading">
          <h1 className="eb-screen-header__title preview-composer__title">{p.title}</h1>
          <p className="preview-composer__adjust-hint eb-caption">{p.adjustHint}</p>
        </div>
        <div className="eb-screen-header__trailing preview-composer__header-actions">
          <button
            type="button"
            onClick={onBack}
            className="mem-btn-secondary preview-composer__change-theme-btn"
          >
            <Palette className="w-4 h-4" strokeWidth={1.5} />
            {p.changeTheme}
          </button>
        </div>
      </header>

      <div className="preview-composer__body flex-1 min-h-0 flex flex-col overflow-y-auto">
      {/* Preview Area — 드래그·핀치로 직접 조절 */}
      <div className="preview-composer__stage flex-1 min-h-0 flex flex-col items-center justify-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="preview-gesture-surface theme-preview-frame relative w-full aspect-[4/3] max-h-[min(42dvh,320px)]"
          /* 자동 생성 상태를 DOM 에 노출한다 — 더블탭이 조용히 죽은 것처럼
             보이지 않게 하고, 런타임 점검도 이 값 하나로 끝난다. */
          data-come-closer={comeCloserState}
          onWheel={handlePreviewWheel}
          onPointerDown={handlePreviewPointerDown}
          onPointerMove={handlePreviewPointerMove}
          onPointerUp={handlePreviewPointerEnd}
          onPointerCancel={handlePreviewPointerEnd}
        >
          <div className="memory-cta-card__shine" />
          {/* 배경 레이어 — 구운 자산에는 **깔지 않는다** (Phase 25).
              영상이 이미 승인된 배경을 담고 있어서, 여기에 한 장 더 깔면 두
              배경이 겹친다. 판정은 baked-playback.ts 한 곳에서만 한다. */}
          {shouldRenderThemeBackdrop(bakedAsset) && (
            <>
              {originalMissing ? (
                // 검은 판을 보여 주지 않는다. 예전에는 원본 테마의 thumb 이
                // 빈 문자열이라 url() 이 아무것도 그리지 않았고, 고객은 검은
                // 사각형을 승인한 뒤 ORIGINAL_PHOTO_MISSING 을 받았다.
                <div
                  role="alert"
                  className="preview-composer__stage-alert absolute inset-0 flex items-center justify-center px-6 text-center"
                >
                  {sceneErrorMessage("ORIGINAL_PHOTO_MISSING", language === "en" ? "en" : "ko")}
                </div>
              ) : previewBgVideo ? (
                <ThemeBackgroundVideo
                  key={`theme-bg-${previewThemeId}-${previewBgVideo}`}
                  src={previewBgVideo}
                  poster={currentTheme.thumb}
                />
              ) : (
                <div
                  className="absolute inset-0 bg-center bg-cover"
                  // 원본 갈래의 배경은 **올린 사진 그 자체**다. 미리보기가 이것을
                  // 보여 줘야 "승인한 그림 = 생성될 그림"이 눈으로 확인된다.
                  style={{ backgroundImage: `url(${originalPhoto || currentTheme.thumb})` }}
                />
              )}
              {/* 원본 갈래에는 테마 색조를 얹지 않는다 — 사진 색이 그대로여야 한다. */}
              {!isOriginalPhotoTheme && (
                <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient} opacity-25`} />
              )}
            </>
          )}

          {/* ── 구운 장면 — **프레임 전체** ────────────────────────────────
              생성된 MP4 가 곧 완성된 그림이다. 누끼용 상자에 넣지 않는다:
              62% 세로 슬롯도, 접지 변환도, 접지 그림자도, 드롭섀도도 없다.

              `!isOriginalPhotoTheme` 게이트를 **타지 않는다.** 그 게이트는
              "사진에 아이가 이미 있으니 누끼를 덧그리지 않는다"는 뜻인데,
              구운 자산은 덧그리는 것이 아니라 그 자체가 장면이다. 예전에는
              원본 갈래로 구운 영상이 아예 렌더되지 않았다. */}
          {!shouldApplySubjectTransform(bakedAsset) ? (
            <div className="absolute inset-0">
              <PetIdleDisplay
                key={`preview-idle-baked-${replayKey}`}
                idleVideoUrl={pipeline?.idle_video_url ?? null}
                cutoutUrl={cutoutDisplay}
                allowDemoFallback={false}
                comeCloserVideoUrl={
                  eligibility.comeCloserAllowed
                    ? (pipeline?.come_closer_video_url ?? null)
                    : null
                }
                idleEventSources={eligibleIdleEventSources}
                eventDeliveryFormats={eventDeliveryFormats}
                actionTriggerRef={comeCloserTriggerRef}
                onActionStateChange={onPlaybackStateChange}
                backgroundBaked
                className={playbackFrameClass(bakedAsset)}
              />
            </div>
          ) : (
            <>
              {/* 접지 그림자 — 피사체 레이어의 형제(자식이 아님)라서 호흡 애니메이션을
                  따라 흔들리지 않는다. 항상 테마 접지선(floorY) 위에 머무른다.
                  가로 이동·크기 조절만 따라간다(세로 드래그는 따라가지 않음 — 땅은 고정). */}
              {(cutoutDisplay || (hasIdle && isLibrarySource)) && !isOriginalPhotoTheme && (
                <div
                  className="preview-contact-shadow"
                  aria-hidden
                  style={{
                    top: `${floorY * 100}%`,
                    transform: `translate(calc(-50% + ${displaySettings.posX}px), -50%) scaleX(${displaySettings.scale})`,
                    opacity: contactShadowOpacity,
                  }}
                />
              )}

              {/* Subject with transformations — first composite with selected theme bg.
                  원본 갈래에서는 그리지 않는다(사진에 아이가 이미 있다).
                  My Library 경로는 정적 누끼가 없다 — hasIdle(발행된 영상)만으로도
                  그려야 한다. cutoutDisplay 만 보면 라이브러리 재생이 빈 화면이 된다. */}
              {(cutoutDisplay || (hasIdle && isLibrarySource)) && !isOriginalPhotoTheme && (
                <div
                  ref={subjectLayerRef}
                  className="absolute inset-0 flex items-end justify-center preview-subject-layer"
                  style={{
                    paddingLeft: "1rem",
                    paddingRight: "1rem",
                    paddingTop: "1rem",
                    // 세로 배치는 transform 으로 한다. padding-bottom 의 % 는 CSS 규격상
                    // 컨테이너 '너비' 기준이라 접지선 계산에 쓸 수 없다. translateY 의 % 는
                    // 요소 자신의 높이 기준이고, 이 레이어는 inset-0(=프레임 높이)이다.
                    transform: subjectTransform({
                      ...displaySettings,
                      shiftPct: subjectShiftPct,
                    }),
                  }}
                >
                  <PetIdleDisplay
                    key={`preview-idle-subject-${replayKey}`}
                    idleVideoUrl={hasIdle ? pipeline?.idle_video_url : null}
                    cutoutUrl={cutoutDisplay}
                    // 생성 전에는 데모 mp4 폴백을 끈다 — 미리보기는 진짜 정적이어야 한다.
                    allowDemoFallback={false}
                    onFeetMarginChange={setFeetMargin}
                    comeCloserVideoUrl={
                      eligibility.comeCloserAllowed
                        ? (pipeline?.come_closer_video_url ?? null)
                        : null
                    }
                    // 적격한 것만 넘긴다 — 소스가 없으면 런타임이 no-source 로 거절한다.
                    idleEventSources={eligibleIdleEventSources}
                eventDeliveryFormats={eventDeliveryFormats}
                    actionTriggerRef={comeCloserTriggerRef}
                    // 스케줄러가 "지금 뭔가 재생 중인가"를 아는 유일한 신호다.
                    onActionStateChange={onPlaybackStateChange}
                    // 이 분기는 정의상 레거시다 — 기본값에 기대지 않고 적는다.
                    backgroundBaked={false}
                    // packed_alpha 는 명시로 선택한다 (Phase 7F).
                    deliveryFormat={breathingDeliveryFormat}
                    staticCutout={pipeline?.delivery_format === "canonical_still"}
                    className={playbackFrameClass(bakedAsset)}
                    style={{
                      filter: `drop-shadow(0 16px 32px ${currentTheme.accent}66)`,
                    }}
                  />
                </div>
              )}
            </>
          )}

          {/* Theme chip — bottom-left, over the imagery. Reuses the same
              themeLabel already shown in the panel; this is just the on-stage
              echo of it that the reference composition calls for. */}
          <div className="preview-composer__theme-chip absolute bottom-3 left-3 z-20">
            <Palette className="w-3.5 h-3.5" strokeWidth={1.75} />
            <span>{themeLabel}</span>
          </div>

          {/* Reset (scale/position) — Create-flow gesture adjustment only. The
              reference composition has no reset control; this keeps the real
              drag/pinch-reset behavior reachable without occupying the header,
              which now carries the title/subtitle/Change Theme instead. */}
          {!isLibraryFlow && hasGestured ? (
            <MemorialIconButton
              onClick={handleReset}
              aria-label={p.reset}
              className="preview-composer__reset-btn absolute top-3 right-3 z-20"
            >
              <RotateCcw className="w-4 h-4" strokeWidth={1.5} />
            </MemorialIconButton>
          ) : null}

          {/* Corner Guides */}
          {["top-3 left-3", "top-3 right-3", "bottom-3 left-3", "bottom-3 right-3"].map((pos, i) => (
            <div key={i} className={`absolute ${pos} w-4 h-4 pointer-events-none`}>
              <div 
                className={`absolute ${i < 2 ? "top-0" : "bottom-0"} ${i % 2 === 0 ? "left-0" : "right-0"} w-3 h-[1px]`}
                style={{ background: `${currentTheme.accent}40` }}
              />
              <div 
                className={`absolute ${i < 2 ? "top-0" : "bottom-0"} ${i % 2 === 0 ? "left-0" : "right-0"} h-3 w-[1px]`}
                style={{ background: `${currentTheme.accent}40` }}
              />
            </div>
          ))}

          {!hasGestured ? (
            <div className="preview-touch-hint absolute inset-x-0 bottom-4 flex justify-center px-4 z-20">
              <span className="preview-composer__touch-chip">
                {p.touchAdjustHint}
              </span>
            </div>
          ) : null}
        </motion.div>
      </div>

      {/* 컨트롤 레일 — 요약 + Change Theme/Motion + Play on Web/Beam.
          발행 전(또는 실물 전달)에는 기존 확인/생성 CTA 를 그대로 보존한다
          (Upload 갈래의 생성/확인 동작은 바꾸지 않는다). */}
      <div className="preview-composer__panel shrink-0">
        {/* Mobile-only theme summary/selector. Desktop already exposes "Change
            Theme" via the header button + the on-stage chip, so this row is
            hidden there (see preview-composer.css) rather than duplicated. */}
        <button type="button" onClick={onBack} className="preview-composer__theme-row">
          <span className="preview-composer__theme-row-label">
            <Palette className="w-4 h-4" strokeWidth={1.5} />
            {themeLabel}
          </span>
          <ChevronDown className="w-4 h-4" strokeWidth={1.5} />
        </button>

        {/* Pet identity — real cutout thumbnail only. Neither a pet name nor a
            breed exists anywhere in the frontend-reachable data today
            (StoredPipeline / LibraryPublication / my-library-api all lack
            it) — showing either would mean inventing profile data, so both
            are omitted rather than faked. */}
        {petAvatarUrl ? (
          <div className="preview-composer__identity">
            <PetPhoto src={petAvatarUrl} variant="avatar" className="preview-composer__identity-avatar" />
          </div>
        ) : null}

        {/* Motion — the real selected/published motion. The chevron (and the
            click-through to reselect) appears only when onChangeMotion was
            handed down, i.e. only when My Library found more than one real
            published motion for this pet. There's no in-place swap today —
            reselecting still returns to the library grid — so this stays a
            truthful affordance rather than a fake inline dropdown. */}
        {motionLabel ? (
          <div className="preview-composer__motion">
            <span className="preview-composer__motion-heading eb-caption">{p.motionHeading}</span>
            {onChangeMotion ? (
              <button type="button" onClick={onChangeMotion} className="preview-composer__motion-select">
                <span className="preview-composer__motion-value">{motionLabel.toLowerCase()}</span>
                <ChevronDown className="w-4 h-4" strokeWidth={1.5} />
              </button>
            ) : (
              <span className="preview-composer__motion-value preview-composer__motion-value--static">
                {motionLabel.toLowerCase()}
              </span>
            )}
          </div>
        ) : null}

        {showPublishedRail ? (
          <>
            <div className="preview-composer__actions-row grid grid-cols-2 gap-3">
              <button
                type="button"
                onClick={handlePlayOnWeb}
                disabled={!hasIdle}
                className="mem-btn-secondary preview-composer__play-web"
              >
                <Play className="w-4 h-4" strokeWidth={1.5} />
                {p.playOnWeb}
              </button>
              <motion.button
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 }}
                onClick={handlePlayOnBeam}
                disabled={!onPlayOnBeam || !hasIdle || beamStatus === "sending"}
                className="preview-composer__cta cta-gold eb-btn-label"
                whileHover={beamStatus === "sending" ? undefined : { scale: 1.02 }}
                whileTap={beamStatus === "sending" ? undefined : { scale: 0.98 }}
              >
                {beamStatus === "sending" ? p.beamSending : p.playOnBeam}
              </motion.button>
            </div>

            {beamStatus !== "idle" ? (
              <div className="preview-composer__status-row flex items-center gap-2 flex-wrap">
                <span
                  className={`eb-status-badge eb-status-badge--${
                    beamStatus === "sent" || beamStatus === "acked"
                      ? "success"
                      : beamStatus === "error"
                        ? "error"
                        : "warning"
                  }`}
                  role="status"
                >
                  {beamStatus === "sending"
                    ? p.beamSending
                    : beamStatus === "acked"
                      ? p.beamAcked
                      : beamStatus === "sent"
                        ? p.beamSent
                        : beamErrorMessage}
                </span>
              </div>
            ) : null}

            <p className="preview-composer__hint">{p.beamHint}</p>

            {/* 내부 QA 컨트롤 — 고객 화면에는 나오지 않는다. 개발 빌드에서
                VITE_INTERNAL_MOTION_QA=1 로 명시적으로 켠 경우에만 렌더된다. */}
            <MotionQAFeedback
              enrolled={!isLibraryFlow && userTestEnrolled}
              language={language}
              status={userTestFeedback}
              onFeedback={(accepted, complaint) => void handleUserTestFeedback(accepted, complaint)}
            />

            {onOpenMembership ? (
              <button
                type="button"
                onClick={onOpenMembership}
                className="mem-btn-secondary preview-composer__membership"
              >
                {p.openMembership}
              </button>
            ) : null}
          </>
        ) : (
          <>
            <p className="preview-composer__hint">
              {p.touchAdjustHint}
              <span className="hidden sm:inline"> · {p.scaleWheelHint}</span>
            </p>
            {genError ? (
              <div role="alert" className="preview-composer__error">
                {genError}
              </div>
            ) : null}
            <motion.button
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 }}
              onClick={handleConfirm}
              disabled={generating || originalMissing}
              className="preview-composer__cta cta-gold eb-btn-label"
              whileHover={generating ? undefined : { scale: 1.02 }}
              whileTap={generating ? undefined : { scale: 0.98 }}
            >
              {generating
                ? p.generating
                : hasIdle
                  ? deliveryMode === "shipping"
                    ? p.completeShipping
                    : p.completeDevice
                  : p.confirmGenerate}
            </motion.button>
          </>
        )}
      </div>
      </div>
    </div>
  );
}
