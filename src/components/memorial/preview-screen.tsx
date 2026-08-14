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
  registeredIdleEvents,
  type IdleEvent,
  type PetRuntimeTrigger,
} from "@/lib/pet-runtime-events";
import { ensureIdleEventAsset } from "@/lib/idle-event-dev-trigger";
import { useIdleEventScheduler } from "@/components/memorial/use-idle-event-scheduler";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { getEternalBeamPetId } from "@/lib/pet-identity";
import {
  isComeCloserCacheValid,
  mergeComeCloserIntoPipeline,
} from "@/lib/come-closer-asset";
import {
  ensureComeCloser,
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

/**
 * 큐가 빠졌는지 다시 물어보는 주기.
 *
 * 짧으면 서버에 헛질문이 늘고, 길면 앞 작업이 끝난 뒤 다음 제출까지 놀게 된다.
 * 생성 자체가 분 단위라 20초면 충분하다.
 */
const IDLE_ASSET_SWEEP_MS = 20_000;

/**
 * COME_CLOSER 가 큐 대기(queued)일 때 재제출을 시도하는 횟수.
 * 20초 × 30 = 약 10분 — 앞선 생성 2건이 끝나기에 충분하다.
 */
const COME_CLOSER_QUEUE_ATTEMPTS = 30;

function pinchDistance(points: Map<number, { x: number; y: number }>) {
  const pts = [...points.values()];
  if (pts.length < 2) return 0;
  return Math.hypot(pts[1].x - pts[0].x, pts[1].y - pts[0].y);
}



export function PreviewScreen({
  cutoutImage,
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
     * 확보 루프.
     *
     * "queued" 를 반드시 다시 시도해야 한다 — 그 상태는 **아직 제출되지 않았다**는
     * 뜻이라, 폴링(GET)만 해서는 영원히 안 나온다. 만들어 줄 작업 자체가 없기
     * 때문이다. 슬롯이 빌 때까지 기다렸다가 **다시 ensure(POST)** 해야 한다.
     * 이 구분을 놓쳐서 COME_CLOSER 가 조용히 버려지고 있었다.
     */
    const acquire = async () => {
      for (let attempt = 0; attempt < COME_CLOSER_QUEUE_ATTEMPTS; attempt += 1) {
        if (cancelled) return;
        const r = await ensureComeCloser({ ...params, onState });
        if (cancelled) return;

        if (r.url) {
          // 값이 실제로 달라질 때만 상태를 바꾼다 — deps=[pipeline] 이라
          // 무조건 setPipeline 하면 렌더 루프가 된다.
          if (r.url !== pipeline.come_closer_video_url || pipeline.come_closer_pet_id !== petId) {
            setPipeline(mergeComeCloserIntoPipeline(pipeline, r.url, petId));
          }
          return;
        }
        // 이 펫의 자산이 없다 = 남아 있는 캐시는 다른 펫 것이다. 즉시 비운다.
        if (pipeline.come_closer_video_url) {
          setPipeline(mergeComeCloserIntoPipeline(pipeline, null, null));
        }

        if (r.state === "generating") {
          // 제출됐다 — 완료되면 수동 새로고침 없이 플레이어에 반영한다.
          const url = await pollComeCloserUntilReady({
            ...params, onState, isCancelled: () => cancelled,
          });
          if (!cancelled && url) setPipeline(mergeComeCloserIntoPipeline(pipeline, url, petId));
          return;
        }
        if (r.state !== "queued") return; // error / unavailable — 재시도 무의미

        // 큐 대기 — 슬롯이 빌 때까지 기다렸다가 다시 제출을 시도한다.
        await new Promise((res) => setTimeout(res, IDLE_ASSET_SWEEP_MS));
      }
    };
    void acquire();

    return () => {
      cancelled = true;
    };
    // 의존성에 테마가 **없다** — 테마 변경이 생성/조회를 유발해선 안 된다.
  }, [pipeline]);

  // ── 아이들 이벤트 (BLINKING / EAR_TWITCHING) — **개발 빌드 전용** ───────────
  // 제품 UI 가 아니다. IdleEvent 파이프라인 점검용 수동 경로다.
  // 자발적 스케줄링은 없다 — 콘솔에서 사람이 부를 때만 재생된다.
  //
  // 이벤트마다 state·effect 를 하나씩 늘리지 않는다. 등록된 아이들 이벤트를
  // 순회하므로, 새 이벤트를 레지스트리에 추가하면 여기 배선은 그대로 따라온다.
  const [idleEventUrls, setIdleEventUrls] = useState<Partial<Record<IdleEvent, string>>>({});
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    if (!pipeline) return;
    let cancelled = false;
    const userId = getEternalBeamUserId();
    const petId = getEternalBeamPetId(pipeline.content_id);

    // 한 바퀴 = 아직 확보되지 않은 이벤트마다 ensure 한 번.
    //
    // 동시 제출 수는 **서버가** 막는다(generation_queue). 여기서 순차 루프를 돌지
    // 않는 이유가 그것이다 — 브라우저 큐는 탭을 두 개 열면 그대로 뚫린다. 프론트는
    // "이 펫 자산 좀 챙겨 줘"라고 반복해서 물을 뿐이고, 상한에 걸린 요청은
    // status=queued 로 조용히 되돌아온다(프로바이더 호출 없음).
    const sweep = async () => {
      let pending = false;
      for (const def of registeredIdleEvents()) {
        if (cancelled) return false;
        const eventId = def.id as IdleEvent;
        const r = await ensureIdleEventAsset({
          userId,
          petId,
          eventId,
          pipeline,
          onState: (st) => console.info(`[${eventId}] asset state =`, st),
        });
        if (cancelled) return false;
        if (r.url) {
          setIdleEventUrls((prev) =>
            prev[eventId] === r.url ? prev : { ...prev, [eventId]: r.url as string }
          );
        } else if (r.state === "queued" || r.state === "generating") {
          pending = true; // 아직 남았다 — 다음 바퀴에서 다시 물어본다
        }
      }
      return pending;
    };

    // 큐가 빠지는 속도에 맞춰 주기적으로 다시 훑는다. 남은 게 없으면 멈춘다.
    let timer: number | null = null;
    const run = () => {
      void sweep().then((pending) => {
        if (cancelled || !pending) return;
        timer = window.setTimeout(run, IDLE_ASSET_SWEEP_MS);
      });
    };
    run();

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [pipeline]);

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

      const pet = await requestIdleGeneration({
        cutFile,
        contentId: meta.contentId,
        // idle 과 COME_CLOSER 가 같은 신원 아래 모이게 한다. 넘기지 않으면
        // 백엔드가 'anonymous' 로 저장해 이후 조회가 영영 어긋난다.
        userId: getEternalBeamUserId(),
      });

      const next: StoredPipeline = {
        content_id: pet.content_id || meta.contentId,
        cutout_display_url: pipeline?.cutout_display_url || meta.displayUrl,
        dog_only_nobg_url: pet.dog_only_nobg_url || meta.displayUrl,
        idle_video_url: pet.idle_video_url || "",
        action_video_url: pet.action_video_url || "",
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
      }

      onComplete();
    } catch (e) {
      setGenError(e instanceof Error ? e.message : String(e));
    } finally {
      generatingRef.current = false;
      setGenerating(false);
    }
  }, [hasIdle, onComplete, p.generateMissingCutout, pipeline?.cutout_display_url]);

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
  // 자산이 하나도 없으면 후보가 비어 아무 일도 일어나지 않는다 — BREATHING 유지.
  const availableIdleEventIds = Object.entries(idleEventUrls)
    .filter(([, url]) => typeof url === "string" && url.length > 0)
    .map(([id]) => id as IdleEvent);

  const { onPlaybackStateChange } = useIdleEventScheduler({
    enabled: availableIdleEventIds.length > 0,
    availableIds: availableIdleEventIds,
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
          fire?.("COME_CLOSER");
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
          {previewBgVideo ? (
            <ThemeBackgroundVideo
              key={`theme-bg-${previewThemeId}-${previewBgVideo}`}
              src={previewBgVideo}
              poster={currentTheme.thumb}
            />
          ) : (
            <div
              className="absolute inset-0 bg-center bg-cover"
              style={{ backgroundImage: `url(${currentTheme.thumb})` }}
            />
          )}
          <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient} opacity-25`} />

          {/* 접지 그림자 — 피사체 레이어의 형제(자식이 아님)라서 호흡 애니메이션을
              따라 흔들리지 않는다. 항상 테마 접지선(floorY) 위에 머무른다.
              가로 이동·크기 조절만 따라간다(세로 드래그는 따라가지 않음 — 땅은 고정). */}
          {cutoutDisplay && (
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

          {/* Subject with transformations — first composite with selected theme bg */}
          {cutoutDisplay && (
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
                transform: subjectTransform({ ...displaySettings, shiftPct: subjectShiftPct }),
              }}
            >
              <PetIdleDisplay
                idleVideoUrl={hasIdle ? pipeline?.idle_video_url : null}
                cutoutUrl={cutoutDisplay}
                // 생성 전에는 데모 mp4 폴백을 끈다 — 미리보기는 진짜 정적이어야 한다.
                allowDemoFallback={false}
                onFeetMarginChange={setFeetMargin}
                comeCloserVideoUrl={pipeline?.come_closer_video_url ?? null}
                // 아이들 이벤트 — DEV 빌드에서만 채워진다(위 effect 가 DEV 게이트).
                idleEventSources={idleEventUrls}
                actionTriggerRef={comeCloserTriggerRef}
                // 스케줄러가 "지금 뭔가 재생 중인가"를 아는 유일한 신호다.
                onActionStateChange={onPlaybackStateChange}
                className="theme-preview-frame__pet max-h-[62%] max-w-[92%]"
                style={{
                  filter: `drop-shadow(0 16px 32px ${currentTheme.accent}66)`,
                }}
              />
            </div>
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
          disabled={generating}
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
