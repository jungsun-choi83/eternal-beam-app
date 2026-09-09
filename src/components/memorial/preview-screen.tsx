"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, RotateCcw, Film } from "lucide-react";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { generatePreview, getVideoApiBaseUrl, resolveIdleVideoUrl } from "@/app/services/videoProcessingApi";
import { memorialT } from "@/components/memorial/memorial-i18n";
import {
  getMemorialTheme,
  DEFAULT_THEME_ID,
} from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";
import { usePetGrounding } from "@/components/memorial/use-pet-grounding";
import { subjectTransform } from "@/lib/pet-grounding";
import {
  playbackFrameClass,
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
import { PremiumAssetsProvider } from "@/components/memorial/premium-assets-context";
import { useBehaviorEligibility } from "@/components/memorial/use-behavior-eligibility";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { ensurePetRegistered } from "@/lib/pet-registry-api";
import { getEternalBeamPetId } from "@/lib/pet-identity";
import { onAuthStateChange } from "@/lib/supabase-auth";
import {
  isComeCloserCacheValid,
  mergeComeCloserIntoPipeline,
} from "@/lib/come-closer-asset";
import {
  lookupComeCloserAsset,
  pollComeCloserUntilReady,
  type ComeCloserState,
} from "@/lib/come-closer-autogen";
import { recognizeTap, type TapPoint } from "@/lib/double-tap";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import {
  getThemeBackgroundApiId,
  resolveSelectedThemeId,
} from "@/lib/theme-selection-store";
import { isLikelyVideoUrl } from "@/lib/video-url";
import {
  getPendingCutoutMeta,
  hasRealIdleVideo,
  rehydrateCutoutFile,
} from "@/lib/pending-generation";
import { requestIdleGeneration } from "@/lib/idle-generation-request";
import { schedulePetReadyToDevice } from "@/lib/device-pet-sync";

interface PreviewScreenProps {
  cutoutImage: string | null;
  /**
   * 원본 갈래에 쓸 **해결된 한 장.** 테마 선택 화면과 같은 값을 부모가 내려 준다.
   * 없으면 저장된 값으로 떨어진다(구버전 호출부 호환).
   */
  originalPhoto?: string | null;
  selectedTheme: number | null;
  language?: string;
  settings: { scale: number; posX: number; posY: number };
  onSettingsChange: (settings: { scale: number; posX: number; posY: number }) => void;
  /** free = 기기 즉시 송출, premium = 배송지 입력으로 */
  deliveryMode?: "device" | "shipping";
  onComplete: () => void;
  onBack: () => void;
}

/** 개발·QA 전용. 프로덕션에서는 조정 화면에 Luma/FFmpeg 패널 숨김 */
const SHOW_PIPELINE_DEBUG =
  import.meta.env.DEV || import.meta.env.VITE_SHOW_PIPELINE_DEBUG === "1";

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

/**
 * 조정 화면도 **같은 런타임**을 돌린다(같은 훅·스케줄러·플레이어). 그래서 적격성
 * 규칙도 같아야 한다 — 여기만 빠지면 만료된 사용자가 조정 화면에서는 프리미엄
 * 행동을 계속 보게 된다.
 */
export function PreviewScreen(props: PreviewScreenProps) {
  const [petId] = useState(() => readPipelinePetId());
  return (
    <PremiumAssetsProvider petId={petId} enabled={petId != null}>
      <PreviewScreenInner {...props} />
    </PremiumAssetsProvider>
  );
}

function PreviewScreenInner({
  cutoutImage,
  originalPhoto: originalPhotoProp = null,
  selectedTheme,
  language = "ko",
  settings,
  onSettingsChange,
  deliveryMode = "device",
  onComplete,
  onBack,
}: PreviewScreenProps) {
  const p = memorialT(language).preview;
  const [displaySettings, setDisplaySettings] = useState(settings);
  const [hasGestured, setHasGestured] = useState(false);
  const [pipeline, setPipeline] = useState<StoredPipeline | null>(null);
  const [ffPreviewUrl, setFfPreviewUrl] = useState<string | null>(null);
  const [ffLoading, setFfLoading] = useState(false);
  const [ffError, setFfError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
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
  const idleVideoUrl = hasIdle
    ? resolveIdleVideoUrl(pipeline?.idle_video_url, cutoutDisplay)
    : "";

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (raw) setPipeline(JSON.parse(raw) as StoredPipeline);
    } catch {
      setPipeline(null);
    }
  }, [cutoutImage]);

  // BREATHING 생성과 레지스트리 등록은 별개다. READY 결과가 있으면 새 생성이든
  // 복원된 파이프라인이든 등록을 보장한다. 인증이 아직 복원되지 않았으면 안전하게
  // 대기하고, Supabase 세션이 돌아오는 즉시 같은 canonical petId 로 재시도한다.
  useEffect(() => {
    if (!hasIdle || !pipeline?.content_id || !pipeline.idle_video_url) return;

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
  }, [hasIdle, pipeline?.content_id, pipeline?.idle_video_url]);

  // 승격된 COME_CLOSER 자산을 확보한다. 없으면 **이 펫·이 테마로 1회만** 생성한다.
  //
  // 새 업로드는 새 content_id → 새 pet_id 를 만든다. 예전 업로드의 COME_CLOSER 를
  // 재사용하지 않는다(다른 사진에서 만든 클립이라 펫이 바뀌어 보인다). 대신 같은
  // 키로 새로 만든다: (user_id, pet_id, place_id, COME_CLOSER).
  //
  // 중복 제출은 서버가 최종적으로 막는다 — 여기 가드는 왕복을 줄일 뿐이다.
  const [comeCloserState, setComeCloserState] = useState<ComeCloserState>("idle");
  // 제스처 핸들러는 useCallback 으로 고정돼 있어 state 를 직접 읽으면 낡은 값을 본다.
  const comeCloserStateRef = useRef<ComeCloserState>("idle");
  comeCloserStateRef.current = comeCloserState;

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
    const petId = getEternalBeamPetId(pipeline.content_id);
    // 캐시는 **같은 펫의 것일 때만** 신뢰한다. 아니면 조회해서 갱신한다 —
    // 조회는 GET 한 번이고, canonical 이 있으면 프로바이더는 불리지 않는다.
    if (isComeCloserCacheValid(pipeline, petId)) return;
    // placeId 를 넘기지 않는다 — COME_CLOSER 는 테마 독립이라 테마를 바꿔도
    // 같은 자산을 그대로 쓴다(재조회·재생성 없음).
    const params = { userId: getEternalBeamUserId(), petId, pipeline };
    let cancelled = false;
    const onState = (st: ComeCloserState) => {
      if (cancelled) return;
      setComeCloserState(st);
      if (import.meta.env.DEV) console.info("[COME_CLOSER] state =", st, petId);
    };

    /**
     * 발견 루프 — **조회만 한다.**
     *
     * 예전에는 여기서 ensureComeCloser() 로 생성을 제출하고, queued 면 재제출까지
     * 했다. 무과금 개발 엔드포인트에서는 편의였지만, 확정된 사업 모델에서
     * COME_CLOSER 는 1 크레딧짜리 구매다 — 화면을 열었다는 이유로 결제가 일어나면
     * 안 된다.
     *
     * 이제 이미 승격됐거나 이미 진행 중인 것만 집어 온다. 없으면 없는 채로 둔다:
     * 소스가 없으면 더블탭이 no-source 로 거절되고 BREATHING 이 유지된다.
     * 새 생성은 purchasePremium(actionKind("COME_CLOSER")) 만 시작한다.
     */
    const acquire = async () => {
      const r = await lookupComeCloserAsset({ ...params, onState });
      if (cancelled) return;

      if (r.url) {
        if (r.url !== pipeline.come_closer_video_url || pipeline.come_closer_pet_id !== petId) {
          setPipeline(mergeComeCloserIntoPipeline(pipeline, r.url, petId));
        }
        return;
      }
      // 이 펫의 자산이 없다 = 남아 있는 캐시는 다른 펫 것이다. 즉시 비운다.
      if (pipeline.come_closer_video_url) {
        setPipeline(mergeComeCloserIntoPipeline(pipeline, null, null));
        return;
      }
      // 이미 진행 중인 작업이 있으면 끝날 때까지 지켜본다(제출은 하지 않는다).
      if (r.state !== "generating") return;
      const url = await pollComeCloserUntilReady({
        ...params, onState, isCancelled: () => cancelled,
      });
      if (!cancelled && url) setPipeline(mergeComeCloserIntoPipeline(pipeline, url, petId));
    };
    void acquire();

    return () => {
      cancelled = true;
    };
    // 의존성에 테마가 **없다** — 테마 변경이 생성/조회를 유발해선 안 된다.
  }, [pipeline, hasIdle]);

  // ── 아이들 이벤트 자산 (4종) — **개발 빌드 전용** ─────────────────────────
  // 스윕 구현은 use-idle-event-assets 로 빠졌다. memorial-device-play-screen 이
  // **같은 훅**을 쓴다 — 이 루프는 유료 생성을 제출하므로 사본이 갈라지면 한쪽에서만
  // 중복 지출이 난다. DEV 게이트와 스윕 주기도 그 훅이 단독으로 갖는다.
  //
  // enabled 는 **실제 BREATH 자산**으로만 판정한다(hasRealIdleVideo) — 데모 폴백
  // mp4 를 근거로 켜면 확인 전에 유료 생성이 나가고 이음매 전제도 깨진다.
  const { urls: idleEventUrls, availableIds: availableIdleEventIds } = useIdleEventAssets({
    pipeline,
    enabled: hasIdle,
  });

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
    setGenerating(true);
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
                    : comeCloserStateRef.current === "unavailable"
                      ? "생성 경로가 꺼져 있다(ENABLE_DEV_PREMIUM_TRIGGER)."
                      : "BREATH 영상이 없거나(정적 누끼) 자산이 아직 없다."),
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

  const tryFfmpegPreview = useCallback(async () => {
    if (!cutoutImage || previewThemeId == null) {
      setFfError(p.cutoutMissing);
      return;
    }
    const bgId = getThemeBackgroundApiId(currentTheme);
    if (!bgId) {
      setFfError(p.themeUnknown);
      return;
    }
    setFfLoading(true);
    setFfError(null);
    setFfPreviewUrl(null);
    try {
      const r = await fetch(cutoutImage);
      const blob = await r.blob();
      const file = new File([blob], "cutout.png", { type: blob.type || "image/png" });
      const { preview_url } = await generatePreview({
        background_id: bgId,
        cutoutFile: file,
        scale: displaySettings.scale,
        position_x: displaySettings.posX,
        position_y: displaySettings.posY,
      });
      const base = getVideoApiBaseUrl();
      setFfPreviewUrl(
        preview_url.startsWith("http") ? preview_url : `${base}${preview_url}`
      );
    } catch (e) {
      setFfError(e instanceof Error ? e.message : p.previewFailed);
    } finally {
      setFfLoading(false);
    }
  }, [cutoutImage, previewThemeId, currentTheme, displaySettings.posX, displaySettings.posY, displaySettings.scale, p.cutoutMissing, p.themeUnknown, p.previewFailed]);

  return (
    <div className="h-full flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
          }}
          whileHover={{ scale: 1.05, borderColor: "#444444" }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F5F5F7" }}
        >
          {p.title}
        </motion.h1>

        <motion.button
          initial={{ opacity: 0, x: 10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={handleReset}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
          }}
          whileHover={{ scale: 1.05, borderColor: "#444444" }}
          whileTap={{ scale: 0.95 }}
        >
          <RotateCcw className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>
      </header>

      <p
        className="px-8 -mt-2 text-center text-xs font-light shrink-0"
        style={{ color: "#888" }}
      >
        {p.adjustHint}
      </p>

      {/* Preview Area — 드래그·핀치로 직접 조절 */}
      <div className="px-6 py-2 flex-1 min-h-0 flex flex-col items-center justify-center">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="preview-gesture-surface theme-preview-frame relative w-full aspect-[3/4] max-h-[min(52dvh,420px)]"
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
                  className="absolute inset-0 flex items-center justify-center px-6 text-center text-[13px] bg-amber-900/30 text-[#e8c97a]"
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
                idleVideoUrl={pipeline?.idle_video_url ?? null}
                cutoutUrl={cutoutDisplay}
                allowDemoFallback={false}
                comeCloserVideoUrl={
                  eligibility.comeCloserAllowed
                    ? (pipeline?.come_closer_video_url ?? null)
                    : null
                }
                idleEventSources={eligibleIdleEventSources}
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
              {cutoutDisplay && !isOriginalPhotoTheme && (
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
                  원본 갈래에서는 그리지 않는다(사진에 아이가 이미 있다). */}
              {cutoutDisplay && !isOriginalPhotoTheme && (
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
                    actionTriggerRef={comeCloserTriggerRef}
                    // 스케줄러가 "지금 뭔가 재생 중인가"를 아는 유일한 신호다.
                    onActionStateChange={onPlaybackStateChange}
                    // 이 분기는 정의상 레거시다 — 기본값에 기대지 않고 적는다.
                    backgroundBaked={false}
                    className={playbackFrameClass(bakedAsset)}
                    style={{
                      filter: `drop-shadow(0 16px 32px ${currentTheme.accent}66)`,
                    }}
                  />
                </div>
              )}
            </>
          )}

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
              <span
                className="rounded-full px-3 py-1.5 text-[10px] font-light tracking-wide text-center"
                style={{
                  color: "rgba(245,245,247,0.88)",
                  background: "rgba(0,0,0,0.55)",
                  border: "1px solid rgba(255,255,255,0.12)",
                }}
              >
                {p.touchAdjustHint}
              </span>
            </div>
          ) : null}
        </motion.div>

        {SHOW_PIPELINE_DEBUG ? (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-5 w-full max-w-[340px] space-y-3"
        >
          <div className="flex items-center gap-2 text-[11px] tracking-wider" style={{ color: "#888" }}>
            <Film className="w-3.5 h-3.5" strokeWidth={1.5} />
            <span>{p.pipelineTitle}</span>
          </div>
          <p className="text-[10px] leading-relaxed" style={{ color: "#666" }}>
            {p.pipelineHint}
          </p>
          {idleVideoUrl || pipeline?.action_video_url ? (
            <div className="grid grid-cols-2 gap-2">
              {idleVideoUrl ? (
                <div className="space-y-1">
                  <span className="text-[9px] uppercase tracking-wider" style={{ color: "#888" }}>
                    {p.idle}
                  </span>
                  <IdleLoopVideo
                    src={idleVideoUrl}
                    transparentComposite={false}
                    className="w-full rounded-lg border border-white/10 max-h-[88px] object-cover bg-black"
                  />
                </div>
              ) : null}
              {pipeline.action_video_url ? (
                <div className="space-y-1">
                  <span className="text-[9px] uppercase tracking-wider" style={{ color: "#888" }}>
                    {p.action}
                  </span>
                  {isLikelyVideoUrl(pipeline.action_video_url) ? (
                    <video
                      src={pipeline.action_video_url}
                      className="w-full rounded-lg border border-white/10 max-h-[88px] object-cover bg-black"
                      controls
                      muted
                      playsInline
                      loop
                    />
                  ) : (
                    <img
                      src={pipeline.action_video_url}
                      alt="Action fallback"
                      className="w-full rounded-lg border border-white/10 max-h-[88px] object-cover bg-black"
                    />
                  )}
                </div>
              ) : null}
            </div>
          ) : (
            <p className="text-[11px] py-2 px-3 rounded-lg" style={{ background: "#1C1C1E", color: "#888" }}>
              {p.noLuma}
            </p>
          )}
          <div
            className="rounded-xl p-3 border border-dashed"
            style={{ borderColor: `${currentTheme.accent}40`, background: "rgba(0,0,0,0.35)" }}
          >
            <p className="text-[11px] font-light mb-2" style={{ color: "#A1A1A6" }}>
              {p.unityPlaceholder}
            </p>
            <button
              type="button"
              onClick={tryFfmpegPreview}
              disabled={ffLoading || !cutoutImage}
              className="w-full py-2 rounded-lg text-[12px] font-normal transition-opacity disabled:opacity-40"
              style={{
                background: "#2a2a2e",
                color: "#E2E2E2",
                border: "1px solid #333",
              }}
            >
              {ffLoading ? p.ffmpegLoading : p.ffmpegTry}
            </button>
            {ffError ? (
              <p className="text-[10px] mt-2" style={{ color: "#c97a7a" }}>
                {ffError}
              </p>
            ) : null}
            {ffPreviewUrl ? (
              <video
                src={ffPreviewUrl}
                className="w-full mt-3 rounded-lg border border-white/10 max-h-[140px] object-contain bg-black"
                controls
                playsInline
              />
            ) : null}
          </div>
        </motion.div>
        ) : null}
      </div>

      {/* 하단 — 슬라이더 없이 완료 버튼만 */}
      <div className="px-8 pb-10 pt-2 shrink-0 space-y-3">
        <p className="text-[10px] text-center font-light" style={{ color: "#6b6b70" }}>
          {p.touchAdjustHint}
          <span className="hidden sm:inline"> · {p.scaleWheelHint}</span>
        </p>
        {genError ? (
          <div
            className="px-4 py-3 rounded-xl text-center text-[13px]"
            style={{
              background: "rgba(80, 20, 20, 0.4)",
              color: "#f5c2c2",
              border: "1px solid #553333",
            }}
          >
            {genError}
          </div>
        ) : null}
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
          onClick={handleConfirm}
          disabled={generating || originalMissing}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider disabled:opacity-70"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            color: "#0a0a0a",
          }}
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
      </div>
    </div>
  );
}
