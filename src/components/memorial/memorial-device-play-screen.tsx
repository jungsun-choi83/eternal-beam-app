"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Radio, WifiOff } from "lucide-react";
import { HolographicBackground } from "@/components/memorial/holographic-background";
import { HologramEffects } from "@/components/memorial/hologram-effects";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { getMemorialTheme, DEFAULT_THEME_ID } from "@/components/memorial/themes";
import { usePetGrounding } from "@/components/memorial/use-pet-grounding";
import { useIdleEventAssets } from "@/components/memorial/use-idle-event-assets";
import { MembershipCard } from "@/components/memorial/membership-card";
import { BehaviorLibrary } from "@/components/memorial/behavior-library";
import { ShakerShareCard } from "@/components/memorial/shaker-share-card";
import { PremiumAssetsProvider } from "@/components/memorial/premium-assets-context";
import { useBehaviorEligibility } from "@/components/memorial/use-behavior-eligibility";
import { useIdleEventScheduler } from "@/components/memorial/use-idle-event-scheduler";
import { hasRealIdleVideo } from "@/lib/pending-generation";
import { subjectTransform } from "@/lib/pet-grounding";
import { resolveIdleDisplaySource } from "@/lib/device-host-flags";
import {
  formatPlaybackSourceReport,
  playbackSourceRows,
} from "@/lib/playback-source-report";
import {
  registeredIdleEvents,
  type IdleEvent,
  type PetRuntimeTrigger,
} from "@/lib/pet-runtime-events";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import {
  broadcastFreeThemeToDevice,
  finalizePreviewContent,
  type PreviewFinalizeSettings,
} from "@/lib/finalize-preview-content";
import { resetThemeBackgroundSyncCache, scheduleThemeBackgroundSync } from "@/lib/device-theme-sync";
import { resolveIdleVideoUrl } from "@/app/services/videoProcessingApi";
import { resolveSelectedThemeId } from "@/lib/theme-selection-store";
import {
  isComeCloserCacheValid,
  mergeComeCloserIntoPipeline,
} from "@/lib/come-closer-asset";
import {
  lookupComeCloserAsset,
  pollComeCloserUntilReady,
  type ComeCloserState,
} from "@/lib/come-closer-autogen";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { getEternalBeamPetId } from "@/lib/pet-identity";
import { recognizeTap, type TapPoint } from "@/lib/double-tap";

interface MemorialDevicePlayScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  settings: PreviewFinalizeSettings;
  language?: string;
  onBack: () => void;
  onComplete: () => void;
  /** 크레딧 충전 화면(설정 > 크레딧)으로 이동. */
  onOpenMembership?: () => void;
  /** 실물 기념품(편지·메모리 박스) 구매 화면으로 이동. */
  onOpenKeepsakes?: () => void;
}

/**
 * 저장된 파이프라인에서 pet_id 만 읽는다 (Provider 에 넘길 값).
 *
 * 본체가 들고 있는 pipeline state 를 쓸 수 없다 — Provider 는 본체보다 **바깥**에
 * 있어야 컨텍스트가 본체까지 흐른다. content_id 는 세션 내내 바뀌지 않으므로
 * 여기서 한 번 읽는 것으로 충분하다.
 */
function readPipelinePetId(): string | null {
  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as StoredPipeline;
    return p.content_id ? getEternalBeamPetId(p.content_id) : null;
  } catch {
    return null;
  }
}

/**
 * 런타임 적격성(구독 ∩ READY ∩ ON)과 멤버십 UI 가 **같은 자산 응답**을 쓰도록
 * 화면 전체를 공유 Provider 로 감싼다.
 *
 * Provider 가 본체 바깥에 있어야 하는 이유: React 컨텍스트는 자식으로만 흐른다.
 * 예전처럼 본체 안에서 카드만 감싸면 스케줄러 배선(본체 상단)이 적격성을 볼 수 없다.
 */
export function MemorialDevicePlayScreen(props: MemorialDevicePlayScreenProps) {
  const [petId] = useState(() => readPipelinePetId());
  return (
    <PremiumAssetsProvider petId={petId} enabled={petId != null}>
      <MemorialDevicePlayScreenInner {...props} />
    </PremiumAssetsProvider>
  );
}

function MemorialDevicePlayScreenInner({
  cutoutImage,
  selectedTheme,
  settings,
  language = "ko",
  onBack,
  onComplete,
  onOpenMembership,
  onOpenKeepsakes,
}: MemorialDevicePlayScreenProps) {
  const d = memorialT(language).devicePlay;
  const themeId = resolveSelectedThemeId(selectedTheme);
  const theme = (themeId != null ? getMemorialTheme(themeId) : undefined) ?? getMemorialTheme(DEFAULT_THEME_ID)!;
  const bgVideo = getEffectiveBgVideo(theme);
  const [pipeline, setPipeline] = useState<StoredPipeline | null>(null);
  const [status, setStatus] = useState<"starting" | "live" | "offline">("starting");
  const [statusHint, setStatusHint] = useState<string | null>(null);

  const cutoutDisplay = useMemo(
    () =>
      cutoutImage ||
      pipeline?.cutout_display_url ||
      pipeline?.dog_only_nobg_url ||
      null,
    [cutoutImage, pipeline]
  );

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (raw) setPipeline(JSON.parse(raw) as StoredPipeline);
    } catch {
      setPipeline(null);
    }
  }, [cutoutImage]);

  // ── COME_CLOSER (프리미엄 1회 액션) ─────────────────────────────────────────
  // 조회 신원은 preview-screen 과 **같은 함수**에서 나온다. 두 화면이 서로 다른
  // 값을 계산하면 한쪽에서만 액션이 보이는 상태가 되고, 그게 원래 버그였다.
  const comeCloserTriggerRef = useRef<PetRuntimeTrigger | null>(null);
  const lastTapRef = useRef<TapPoint | null>(null);
  const tapStartRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    if (!pipeline) return;
    const petId = getEternalBeamPetId(pipeline.content_id);
    if (isComeCloserCacheValid(pipeline, petId)) return;
    let cancelled = false;
    // 테마 독립 — placeId 를 넘기지 않고, 의존성에도 테마가 없다.
    const params = { userId: getEternalBeamUserId(), petId, pipeline };
    const onState = (st: ComeCloserState) => {
      if (import.meta.env.DEV) console.info("[COME_CLOSER/devicePlay] state =", st);
    };

    void (async () => {
      const r = await lookupComeCloserAsset({ ...params, onState });
      if (cancelled) return;

      if (r.url) {
        if (r.url !== pipeline.come_closer_video_url || pipeline.come_closer_pet_id !== petId) {
          setPipeline(mergeComeCloserIntoPipeline(pipeline, r.url, petId));
        }
        return;
      }

      if (pipeline.come_closer_video_url) {
        setPipeline(mergeComeCloserIntoPipeline(pipeline, null, null)); // 다른 펫 캐시 제거
        return; // 파이프라인이 바뀌어 이 effect 가 다시 돈다 — 폴링은 그 회차에서 시작한다
      }

      // ⚠️ 여기가 빠져 있어서 COME_CLOSER 가 no-source 로 굳었다.
      //
      // 이 화면은 ensure 를 **한 번** 부르고 끝이었다. 그런데 사용자가 조정 화면에서
      // 넘어온 직후에는 COME_CLOSER 가 아직 queued/generating 인 경우가 흔하고, 그때
      // r.url 은 null 이다. 그러면 come_closer_video_url 이 영영 채워지지 않아
      // mountableEvents 가 COME_CLOSER 를 빼고, 더블탭은 decideTrigger 에서
      // hasSource=false → "no-source" 로 거절된다. 자산이 나중에 승격돼도 이 화면은
      // 다시 물어보지 않으므로 새로고침 없이는 절대 재생되지 않았다.
      //
      // queued 도 폴링으로 해결된다 — 서버가 종료 이벤트마다 큐를 전진시키고
      // (premium_generation.advance_generation_queue) COME_CLOSER 는 GENERATION_ORDER
      // 1순위라, 슬롯이 비면 제출된다. 그래서 이 화면은 재제출하지 않고 기다리기만 한다.
      if (r.state !== "generating") return;
      const url = await pollComeCloserUntilReady({
        ...params,
        onState,
        isCancelled: () => cancelled,
      });
      if (cancelled || !url) return;
      setPipeline(mergeComeCloserIntoPipeline(pipeline, url, petId));
    })();

    return () => {
      cancelled = true;
    };
  }, [pipeline]);

  // ── 아이들 이벤트 4종 (BLINKING / EAR_TWITCHING / HEAD_TILTING / TAIL_WAGGING) ──
  //
  // preview-screen 과 **완전히 같은 배선**이다: 같은 자산 훅, 같은 스케줄러, 같은
  // 트리거 핸들(comeCloserTriggerRef). 런타임(pet-runtime-events / idle-loop-video /
  // 이음매 전환 / 우선순위)은 화면을 모른다 — 소스 표만 채워 주면 그대로 동작한다.
  //
  // 게이트는 **실제 BREATH 자산**이다. 이 화면의 petIdleSrc 는 resolveIdleVideoUrl 을
  // 거치므로 데모 폴백 mp4 일 수 있어(device-host-flags) 재생 소스로는 판정 근거가
  // 되지 못한다. 데모를 근거로 켜면 (1) BREATH 가 없는 펫에 유료 생성 4건이 나가고
  // (2) 이벤트의 seam-aligned 복귀가 다른 개의 휴지 자세에 맞춰져 이음매가 보인다.
  const hasIdle = hasRealIdleVideo(pipeline);
  const { urls: idleEventUrls, availableIds: availableIdleEventIds } = useIdleEventAssets({
    pipeline,
    enabled: hasIdle,
  });

  // ── 런타임 적격성 ─────────────────────────────────────────────────────────
  // 구독 entitled ∩ 자산 READY ∩ 선호 ON. 스케줄러·플레이어·런타임은 한 줄도
  // 바뀌지 않는다 — **입력만 좁힌다**. 만료되면 후보와 소스가 함께 비고,
  // 스케줄러는 자기 규칙대로 조용히 멈춘다. BREATHING 은 이 판정 밖이라 계속 돈다.
  const eligibility = useBehaviorEligibility();
  const eligibleIdleEventIds = eligibility.filterIds(availableIdleEventIds);
  const eligibleIdleEventSources = eligibility.filterSources(idleEventUrls);
  const comeCloserSource = eligibility.comeCloserAllowed
    ? (pipeline?.come_closer_video_url ?? null)
    : null;

  // 탭 핸들러는 deps 가 빈 useCallback 이다(제스처 판정이 재생성되면 탭 상태가
  // 끊긴다). 적격성은 렌더마다 바뀔 수 있으므로 ref 로 읽는다 — 핸들러의 배선을
  // 건드리지 않으면서 최신 값을 본다.
  const comeCloserAllowedRef = useRef(eligibility.comeCloserAllowed);
  comeCloserAllowedRef.current = eligibility.comeCloserAllowed;

  // 자산이 하나도 없으면 후보가 비어 아무 일도 일어나지 않는다 — BREATHING 유지.
  // 더블탭 COME_CLOSER 와 진입점을 공유하므로 우선순위·선점 판정은 decideTrigger 가
  // 단독으로 쥔다(COME_CLOSER 100/non-interruptible vs 아이들 10/interruptible).
  const { onPlaybackStateChange } = useIdleEventScheduler({
    enabled: eligibleIdleEventIds.length > 0,
    availableIds: eligibleIdleEventIds,
    triggerRef: comeCloserTriggerRef,
  });

  const handlePetPointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    tapStartRef.current = { x: e.clientX, y: e.clientY };
  }, []);

  // 이 화면에는 드래그·핀치가 없다(위치 조절은 preview 에서 끝났다). 그래서
  // 제스처 경합 없이 pointerup 만으로 더블탭을 판정할 수 있다.
  const handlePetPointerUp = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const start = tapStartRef.current;
    tapStartRef.current = null;
    const r = recognizeTap(
      start ?? undefined,
      { t: Date.now(), x: e.clientX, y: e.clientY },
      lastTapRef.current
    );
    if (r.kind === "double") {
      lastTapRef.current = null;
      // 더블탭 ∩ 구독 ∩ READY ∩ ON. 자발적 경로와 **같은 규칙**을 쓴다 —
      // 갈라지면 만료된 사용자가 더블탭으로만 프리미엄을 계속 쓸 수 있다.
      if (comeCloserAllowedRef.current) comeCloserTriggerRef.current?.("COME_CLOSER");
    } else if (r.kind === "first") {
      lastTapRef.current = r.tap;
    }
  }, []);

  const rebroadcastToDevice = useCallback(async () => {
    if (themeId == null) return;
    setStatus("starting");
    setStatusHint(d.startingHint);
    resetThemeBackgroundSyncCache();
    scheduleThemeBackgroundSync(themeId);
    try {
      const contentId = await finalizePreviewContent(themeId, settings);
      const ok = await broadcastFreeThemeToDevice(theme, contentId);
      if (ok) {
        setStatus("live");
        setStatusHint(d.liveHint);
      } else {
        setStatus("offline");
        setStatusHint(d.offlineHint);
      }
    } catch {
      setStatus("offline");
      setStatusHint(d.offlineHint);
    }
  }, [themeId, theme, settings, d.startingHint, d.liveHint, d.offlineHint]);

  useEffect(() => {
    void rebroadcastToDevice();
  }, [rebroadcastToDevice]);

  const idleVideoUrl = resolveIdleVideoUrl(pipeline?.idle_video_url, cutoutDisplay);
  const petIdleSrc = idleVideoUrl ?? pipeline?.idle_video_url;

  // 접지 — preview-screen 과 **같은 훅**. 예전에는 이 화면에만 계산이 없어서
  // (items-center + 보정 없는 transform) 펫이 테마 지면이 아니라 프레임
  // 한가운데에 떠 있었다.
  const { setFeetMargin, subjectShiftPct } = usePetGrounding(theme, petIdleSrc);

  // ── 재생 소스 진단 (DEV 전용) ─────────────────────────────────────────────
  // 눈으로는 진짜 자산과 데모/CSS 를 구분할 수 없다 — 셋 다 "숨 쉬는 개"로 보인다.
  // IdleLoopVideo 에 들어가기 **직전** 값을 그대로 찍어, 어느 소스가 real /
  // fallback / missing 인지와 BREATH 가 <video> 인지 CutoutIdleMotion(CSS)인지를
  // 한 번에 읽을 수 있게 한다. 판정만 한다 — 재생 동작에는 영향이 없다.
  const comeCloserSrc = pipeline?.come_closer_video_url ?? null;
  useEffect(() => {
    if (!import.meta.env.DEV) return;
    const display = resolveIdleDisplaySource(petIdleSrc, cutoutDisplay);
    const rows = playbackSourceRows([
      ["BREATHING", petIdleSrc],
      ["COME_CLOSER", comeCloserSrc],
      ...registeredIdleEvents().map(
        (def) => [def.id, idleEventUrls[def.id as IdleEvent] ?? null] as const
      ),
    ]);
    console.info(
      `[devicePlay] 재생 소스 (BREATH mode=${display?.mode ?? "none"}, ` +
        `hasRealIdleVideo=${hasIdle})\n${formatPlaybackSourceReport(rows)}`
    );
  }, [petIdleSrc, cutoutDisplay, comeCloserSrc, idleEventUrls, hasIdle]);

  return (
    <div className="hologram-bg-active memorial-screen-shell h-full flex flex-col relative overflow-hidden min-h-0">
      <HolographicBackground />
      <HologramEffects />

      <header className="px-6 pt-8 pb-4 relative z-10 shrink-0 flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="mem-icon-btn relative shrink-0"
          style={{
            background: "rgba(255, 255, 255, 0.08)",
            borderColor: "rgba(255, 255, 255, 0.14)",
          }}
        >
          <ArrowLeft className="w-5 h-5" style={{ color: "#E2E2E2" }} />
        </button>
        <div className="text-center flex-1 px-3">
          <p className="logo-subtitle text-[11px] tracking-[0.28em] opacity-80">ETERNAL BEAM</p>
          <p className="text-sm font-light mt-1" style={{ color: "#F5F5F7" }}>
            {d.title}
          </p>
        </div>
        <div className="w-10" aria-hidden />
      </header>

      <div className="flex-1 px-6 pb-4 relative z-10 flex flex-col items-center min-h-0">
        <motion.div
          initial={{ opacity: 0, scale: 0.96 }}
          animate={{ opacity: 1, scale: 1 }}
          // shrink-0 이 **필수**다. 이 프레임의 내용물은 전부 absolute inset-0 이라
          // 내재 높이가 0 이고, flex 자식은 기본이 flex-shrink:1 이다. 아래에 형제가
          // 늘어나 컨테이너가 넘치는 순간 프레임이 0 까지 눌려, aspect-[3/4] 의 너비만
          // 남은 납작한 가로 막대가 된다(잠금 카드를 추가하면서 실제로 그렇게 됐다).
          className="theme-preview-frame relative w-full aspect-[3/4] max-h-[min(52vh,360px)] shrink-0"
          onPointerDown={handlePetPointerDown}
          onPointerUp={handlePetPointerUp}
          onPointerCancel={handlePetPointerUp}
        >
          {bgVideo ? (
            <ThemeBackgroundVideo
              key={`live-bg-${theme.id}-${bgVideo}`}
              src={bgVideo}
              poster={theme.thumb}
            />
          ) : (
            <div
              className="absolute inset-0 bg-center bg-cover"
              style={{ backgroundImage: `url(${theme.thumb})` }}
            />
          )}
          <div className={`absolute inset-0 bg-gradient-to-b ${theme.gradient} opacity-25`} />

          {cutoutDisplay ? (
            <div
              className="absolute inset-0 flex items-end justify-center preview-subject-layer"
              style={{
                // 패딩은 preview-screen 과 동일하게 좌·우·상만 준다. 하단 패딩을
                // 주면 items-end 기준선이 그만큼 올라가 접지 계산이 어긋난다.
                paddingLeft: "1rem",
                paddingRight: "1rem",
                paddingTop: "1rem",
                transform: subjectTransform({ ...settings, shiftPct: subjectShiftPct }),
              }}
            >
              <PetIdleDisplay
                idleVideoUrl={petIdleSrc}
                cutoutUrl={cutoutDisplay}
                comeCloserVideoUrl={comeCloserSource}
                // 적격한 것만 넘긴다 — 소스가 없으면 런타임이 자기 규칙(no-source)으로
                // 거절하므로, 수동 트리거가 남아 있어도 OFF 는 재생되지 않는다.
                idleEventSources={eligibleIdleEventSources}
                actionTriggerRef={comeCloserTriggerRef}
                // 스케줄러가 "지금 뭔가 재생 중인가"를 아는 유일한 신호다.
                // 이 prop 없이 스케줄러만 붙이면 COME_CLOSER 재생 중에도 발화해서
                // 거절 → 재예약 루프를 돈다(busyRef 가 영영 true 가 되지 않는다).
                onActionStateChange={onPlaybackStateChange}
                onFeetMarginChange={setFeetMargin}
                className="theme-preview-frame__pet max-h-[62%] max-w-[92%]"
                style={{
                  filter: `drop-shadow(0 16px 32px ${theme.accent}66)`,
                }}
              />
            </div>
          ) : null}
        </motion.div>

        {/* 프레임 **아래쪽만** 스크롤한다.
            재생 영역은 위에서 shrink-0 으로 고정했으므로, 세로 공간이 모자랄 때
            줄어드는 것은 이 영역이다. 예전에는 이 아래 형제들이 프레임을 눌러
            납작하게 만들었다. */}
        <div className="w-full flex-1 min-h-0 overflow-y-auto hide-scrollbar flex flex-col items-center">
          {/* 멤버십 카드 — 재생 프레임 바로 아래, 상태 표시 위.
              여기에 두는 이유: 멤버십이 바꾸는 것(자발적 움직임·더블탭)이 바로 위
              프레임에서 일어나므로, 결과가 보이는 자리에서 사는 것이 가장 짧은 경로다.
              새 화면을 만들지 않는다. */}
          {/* 멤버십 카드·행동 라이브러리·런타임 적격성이 **같은 자산 응답**을
              나눠 쓴다. Provider 는 화면 최상단에 한 번만 있다. */}
          <div className="mt-4 flex flex-col items-center gap-3 w-full shrink-0">
              <MembershipCard
                enabled={hasIdle}
                language={language}
                onOpenMembership={onOpenMembership}
              />
              {/* 행동 라이브러리 — 활성 멤버에게만 그려진다(컴포넌트가 스스로 판단).
                  멤버십 카드 바로 아래에 두는 이유: 가입 → 무엇을 만들지 고르기가
                  한 화면에서 이어져야 한다. */}
              <BehaviorLibrary
                petId={pipeline ? getEternalBeamPetId(pipeline.content_id) : null}
                petImageUrl={pipeline?.dog_only_nobg_url ?? null}
                enabled={hasIdle}
                language={language}
            />
              {/* QR 공유 — 행동 라이브러리 아래. "이 아이를 만든다 → 남에게 보여 준다"가
                  같은 화면에서 이어진다. 새 화면을 만들지 않는다.
                  펫을 복제하지 않는다: 이미 있는 content_id 에서 파생된 pet_id 와
                  이미 생성된 BREATHING URL 을 **가리키기만** 한다. */}
              {/* 기념품(실물) 진입. 카드 스택의 마지막 — 멤버십/행동/QR 다음에
                  "이 아이를 손에 남긴다"가 온다. 여기서 주문을 만들지 않는다:
                  화면을 열 뿐이고, 펫도 편지도 새로 만들어지지 않는다. */}
              {onOpenKeepsakes && hasIdle && (
                <button
                  type="button"
                  onClick={onOpenKeepsakes}
                  className="w-full max-w-[320px] rounded-2xl border px-4 py-3.5 text-left backdrop-blur-sm"
                  style={{
                    background: "rgba(255,255,255,0.04)",
                    borderColor: "rgba(255,255,255,0.12)",
                  }}
                >
                  <p className="text-sm font-medium" style={{ color: "#F2F2F2" }}>
                    기념품 만들기
                  </p>
                  <p className="mt-1 text-[12px] leading-relaxed" style={{ color: "#B8B8B8" }}>
                    편지 ₩14,900 · 메모리 박스 ₩49,000
                  </p>
                  <p className="mt-1 text-[11px]" style={{ color: "#8a8a8a" }}>
                    Soul Trace 편지와 QR 을 실물로 보내 드립니다.
                  </p>
                </button>
              )}
              <ShakerShareCard
                petId={pipeline ? getEternalBeamPetId(pipeline.content_id) : null}
                breathingUrl={hasRealIdleVideo(pipeline) ? (pipeline?.idle_video_url ?? null) : null}
                posterCandidates={[
                  pipeline?.dog_only_nobg_url,
                  pipeline?.cutout_display_url,
                ]}
                enabled={hasIdle}
                language={language}
              />
          </div>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-5 shrink-0 flex flex-col items-center gap-2 text-center max-w-[280px]"
        >
          <button
            type="button"
            onClick={() => void rebroadcastToDevice()}
            className="flex flex-col items-center gap-2 text-center bg-transparent border-0 p-2 cursor-pointer"
            aria-label={d.liveTitle(theme.nameKo || theme.name)}
          >
          {status === "live" ? (
            <Radio className="w-7 h-7 text-emerald-300/90" strokeWidth={1.25} />
          ) : status === "offline" ? (
            <WifiOff className="w-7 h-7 text-amber-200/80" strokeWidth={1.25} />
          ) : (
            <motion.div
              className="w-7 h-7 rounded-full border-2 border-[#c9a227]/40 border-t-[#c9a227]"
              animate={{ rotate: 360 }}
              transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
            />
          )}
          <p className="text-base font-medium" style={{ color: "#F1E5D1" }}>
            {status === "starting"
              ? d.starting
              : status === "live"
                ? d.liveTitle(theme.nameKo || theme.name)
                : d.offlineTitle}
          </p>
          <p className="text-sm memorial-body">{statusHint ?? d.startingHint}</p>
          </button>
        </motion.div>
        </div>
      </div>

      <div className="px-8 pb-10 shrink-0 relative z-10">
        <motion.button
          type="button"
          onClick={onComplete}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider"
          style={{
            background: "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
            boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
            color: "#0a0a0a",
          }}
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
        >
          {d.done}
        </motion.button>
      </div>
    </div>
  );
}
