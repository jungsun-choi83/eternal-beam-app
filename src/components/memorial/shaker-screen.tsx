"use client";

/**
 * QR Shaker — 로그인 없이 펫 하나를 전체 화면으로 보는 공개 화면.
 *
 * ── 이 화면이 **하지 않는** 것 ───────────────────────────────────────────────
 * 인증하지 않는다. 생성하지 않는다. 앱 상태(pipeline / premium context / 테마
 * 선택 / 기기 동기화)를 읽지도 쓰지도 않는다. App.tsx 가 메인 앱 트리를 아예
 * 마운트하지 않고 이 화면으로 분기하므로, 그쪽의 effect 가 돌 일이 없다.
 *
 * ── 재생은 전부 재사용이다 ───────────────────────────────────────────────────
 * BREATHING 루프, 더블탭 1회 재생, 끝나면 자동 복귀, 실패 시 복구는 전부
 * IdleLoopVideo 가 이미 한다. 여기서 새로 만든 것은 껍데기(전체 화면·포스터·
 * 상태 문구)와 패럴랙스뿐이다.
 *
 * ── 문구를 지역 사전으로 두는 이유 ───────────────────────────────────────────
 * memorial-i18n 을 쓰지 않는다. 그쪽은 메인 앱 화면들의 사전이고, 공개 페이지가
 * 거기에 얽히면 앱 문구를 고칠 때 QR 로 들어온 사람이 보는 화면이 같이 흔들린다.
 * 여기서 쓰는 문구는 열 줄이 안 되므로 자급자족이 더 안전하다.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";
import { shouldTransparentComposite } from "@/lib/baked-playback";
import { recognizeTap, type TapPoint } from "@/lib/double-tap";
import type { PetRuntimeTrigger } from "@/lib/pet-runtime-events";
import {
  ShakerApiError,
  fetchShakerPet,
  type ShakerErrorCode,
  type ShakerPet,
} from "@/lib/shaker-api";
import { currentShakerParams, resolveShakerEntry } from "@/lib/shaker-entry";
import {
  NO_PARALLAX,
  createParallaxTracker,
  detectGyroSupport,
  pointerToGyroSample,
  prefersReducedMotion,
  requestGyroPermission,
  shouldAnimateParallax,
  type GyroPermission,
  type ParallaxFrame,
} from "@/lib/shaker-gyro";
import { buildShakerViewModel } from "@/lib/shaker-playback";

type Lang = "ko" | "en";

const T = {
  ko: {
    loading: "불러오는 중…",
    doubleTapHint: "화면을 두 번 탭해 보세요",
    enableMotion: "움직임 효과 켜기",
    retry: "다시 시도",
    unavailable: "지금은 볼 수 없어요",
    reasons: {
      "missing-token": "이 주소에는 공유 링크가 없어요. QR 을 다시 스캔해 주세요.",
      "malformed-token": "링크가 손상된 것 같아요. QR 을 다시 스캔해 주세요.",
      SHARE_NOT_FOUND: "유효하지 않은 링크예요. QR 을 다시 스캔해 주세요.",
      SHARE_REVOKED: "이 링크는 더 이상 사용되지 않아요.",
      SHARE_EXPIRED: "이 링크는 기간이 지났어요.",
      SHARE_TOKEN_REQUIRED: "링크가 올바르지 않아요.",
      SHARE_ASSET_UNAVAILABLE: "영상을 불러올 수 없어요. 잠시 후 다시 시도해 주세요.",
      RATE_LIMITED: "잠시 후 다시 시도해 주세요.",
      SHARE_STORE_UNAVAILABLE: "잠시 문제가 있어요. 다시 시도해 주세요.",
      NETWORK: "네트워크에 연결되지 않았어요.",
      UNKNOWN: "잠시 후 다시 시도해 주세요.",
    },
  },
  en: {
    loading: "Loading…",
    doubleTapHint: "Double-tap the screen",
    enableMotion: "Enable motion",
    retry: "Try again",
    unavailable: "Not available right now",
    reasons: {
      "missing-token": "This address has no share link. Please scan the QR again.",
      "malformed-token": "This link looks damaged. Please scan the QR again.",
      SHARE_NOT_FOUND: "This link isn't valid. Please scan the QR again.",
      SHARE_REVOKED: "This link is no longer active.",
      SHARE_EXPIRED: "This link has expired.",
      SHARE_TOKEN_REQUIRED: "This link isn't valid.",
      SHARE_ASSET_UNAVAILABLE: "The video couldn't be loaded. Please try again shortly.",
      RATE_LIMITED: "Please try again in a moment.",
      SHARE_STORE_UNAVAILABLE: "Something went wrong. Please try again.",
      NETWORK: "You appear to be offline.",
      UNKNOWN: "Please try again in a moment.",
    },
  },
} as const;

function detectLang(): Lang {
  if (typeof navigator === "undefined") return "ko";
  return (navigator.language || "").toLowerCase().startsWith("en") ? "en" : "ko";
}

type LoadState =
  | { phase: "loading" }
  | { phase: "ready"; pet: ShakerPet }
  | { phase: "error"; code: ShakerErrorCode | "missing-token" | "malformed-token"; retryable: boolean };

export function ShakerScreen() {
  const lang = useMemo(detectLang, []);
  const t = T[lang];

  const entry = useMemo(() => resolveShakerEntry(currentShakerParams()), []);
  const [state, setState] = useState<LoadState>({ phase: "loading" });
  const [attempt, setAttempt] = useState(0);

  // ── 펫 불러오기 ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (entry.kind !== "ready") {
      setState({ phase: "error", code: entry.kind, retryable: false });
      return;
    }
    const ac = new AbortController();
    setState({ phase: "loading" });
    fetchShakerPet({ token: entry.token, petId: entry.petId, signal: ac.signal })
      .then((pet) => setState({ phase: "ready", pet }))
      .catch((e) => {
        if ((e as { name?: string })?.name === "AbortError") return;
        const err = e instanceof ShakerApiError ? e : null;
        setState({
          phase: "error",
          code: err?.code ?? "UNKNOWN",
          retryable: err?.retryable ?? true,
        });
      });
    return () => ac.abort();
  }, [entry, attempt]);

  // ── 패럴랙스 ───────────────────────────────────────────────────────────────
  const reducedMotion = useMemo(prefersReducedMotion, []);
  const gyroSupport = useMemo(detectGyroSupport, []);
  const [permission, setPermission] = useState<GyroPermission | null>(
    // Android/데스크톱 크롬은 요청 절차가 없다 — 지원하면 곧바로 구독한다.
    // iOS 는 사용자 제스처가 필요하므로 버튼을 눌러야 granted 가 된다.
    () => (detectGyroSupport() === "auto" ? "granted" : null)
  );
  const [pointerFallback, setPointerFallback] = useState(false);
  const [frame, setFrame] = useState<ParallaxFrame>(NO_PARALLAX);
  const trackerRef = useRef(createParallaxTracker());

  const animate = shouldAnimateParallax({
    permission,
    reducedMotion,
    pointerFallbackActive: pointerFallback,
  });

  // 자이로 구독. 움직임 최소화가 켜져 있으면 **아예 구독하지 않는다** —
  // 구독해 두고 값만 버리면 배터리를 쓰면서 아무것도 하지 않는 셈이다.
  useEffect(() => {
    if (reducedMotion) return;
    if (permission !== "granted") return;
    if (typeof window === "undefined") return;

    const onOrient = (e: DeviceOrientationEvent) => {
      setFrame(trackerRef.current.push({ beta: e.beta, gamma: e.gamma }));
    };
    window.addEventListener("deviceorientation", onOrient);
    return () => window.removeEventListener("deviceorientation", onOrient);
  }, [permission, reducedMotion]);

  // 비-자이로 폴백. 자이로가 없거나 거부됐을 때만 포인터를 쓴다.
  // 데스크톱에서 마우스를 움직이면 같은 깊이감이 나고, 아무것도 안 하면 정지 상태다.
  useEffect(() => {
    if (reducedMotion) return;
    if (permission === "granted") return;
    if (gyroSupport === "ios-permission" && permission === null) return; // 아직 물어보는 중
    if (typeof window === "undefined") return;

    const onMove = (e: PointerEvent) => {
      setPointerFallback(true);
      setFrame(
        trackerRef.current.push(
          pointerToGyroSample(
            { x: e.clientX, y: e.clientY },
            { width: window.innerWidth, height: window.innerHeight }
          )
        )
      );
    };
    window.addEventListener("pointermove", onMove, { passive: true });
    return () => window.removeEventListener("pointermove", onMove);
  }, [permission, reducedMotion, gyroSupport]);

  // 방향 전환 시 기준 자세를 다시 잡는다 — 안 그러면 세로에서 잡은 기준이
  // 가로에서 최대치로 붙어 버린다.
  useEffect(() => {
    const onOrientationChange = () => trackerRef.current.reset();
    window.addEventListener("orientationchange", onOrientationChange);
    return () => window.removeEventListener("orientationchange", onOrientationChange);
  }, []);

  const askMotion = useCallback(async () => {
    // **반드시 이 클릭 핸들러 안에서** 부른다 — 제스처 밖이면 Safari 가 조용히 거부한다.
    const result = await requestGyroPermission();
    setPermission(result);
    trackerRef.current.reset();
  }, []);

  // ── 더블탭 ─────────────────────────────────────────────────────────────────
  const triggerRef = useRef<PetRuntimeTrigger | null>(null);
  const lastTapRef = useRef<TapPoint | null>(null);
  const pointerStartRef = useRef<{ x: number; y: number } | undefined>(undefined);

  const vm = state.phase === "ready" ? buildShakerViewModel(state.pet) : null;
  const doubleTap = vm?.doubleTap;

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    pointerStartRef.current = { x: e.clientX, y: e.clientY };
  }, []);

  const onPointerUp = useCallback(
    (e: React.PointerEvent) => {
      const end: TapPoint = { t: Date.now(), x: e.clientX, y: e.clientY };
      const result = recognizeTap(pointerStartRef.current, end, lastTapRef.current);
      pointerStartRef.current = undefined;

      if (result.kind === "drag") return;
      if (result.kind === "first") {
        lastTapRef.current = result.tap;
        return;
      }
      // 더블탭 성립. 정책이 허락한 액션이 없으면 아무 일도 하지 않는다 —
      // trigger 도 소스가 없으면 스스로 거절하므로 이중으로 안전하다.
      lastTapRef.current = null;
      if (doubleTap?.available) triggerRef.current?.(doubleTap.actionId);
    },
    [doubleTap]
  );

  // ── 포스터 ─────────────────────────────────────────────────────────────────
  const [firstFrameShown, setFirstFrameShown] = useState(false);
  const onFirstFrame = useCallback(() => setFirstFrameShown(true), []);
  useEffect(() => {
    // 펫이 바뀌면(재시도 등) 포스터를 다시 세운다.
    setFirstFrameShown(false);
  }, [vm?.breathingUrl]);

  // ── 렌더 ───────────────────────────────────────────────────────────────────
  if (state.phase === "error") {
    return (
      <ShakerShell frame={NO_PARALLAX} animate={false}>
        <div className="flex h-full w-full flex-col items-center justify-center gap-3 px-8 text-center">
          <p className="text-base font-medium text-white/90">{t.unavailable}</p>
          <p className="max-w-xs text-sm leading-relaxed text-white/55">
            {t.reasons[state.code]}
          </p>
          {state.retryable && (
            <button
              type="button"
              onClick={() => setAttempt((n) => n + 1)}
              className="mt-2 rounded-full border border-white/20 px-5 py-2 text-sm text-white/80 active:bg-white/10"
            >
              {t.retry}
            </button>
          )}
        </div>
      </ShakerShell>
    );
  }

  if (state.phase === "loading" || !vm) {
    return (
      <ShakerShell frame={NO_PARALLAX} animate={false}>
        <div className="flex h-full w-full items-center justify-center">
          <p className="text-sm text-white/40">{t.loading}</p>
        </div>
      </ShakerShell>
    );
  }

  return (
    <ShakerShell frame={frame} animate={animate}>
      <div
        className="relative h-full w-full touch-none select-none"
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          pointerStartRef.current = undefined;
        }}
      >
        {/* 펫 레이어 — 배경보다 많이 움직여 앞에 있는 것처럼 보인다. */}
        <div
          className="absolute inset-0 flex items-end justify-center"
          style={{
            transform: animate
              ? `translate3d(${frame.pet.x.toFixed(2)}px, ${frame.pet.y.toFixed(2)}px, 0)`
              : undefined,
            // 감쇠는 트래커가 이미 한다. 여기 transition 을 걸면 두 번 감쇠돼 늘어진다.
            willChange: animate ? "transform" : undefined,
          }}
        >
          <IdleLoopVideo
            src={vm.breathingUrl}
            eventSources={vm.eventSources}
            actionTriggerRef={triggerRef}
            onFirstFrame={onFirstFrame}
            className="h-full w-full"
            preload="auto"
            // QR 재생도 같은 규칙을 쓴다 — 구운 자산이면 키잉하지 않는다.
            transparentComposite={shouldTransparentComposite({
              backgroundBaked: vm.backgroundBaked,
            })}
          />
        </div>

        {/* 포스터 — 첫 프레임이 올 때까지 덮는다. 검은 화면을 보여 주지 않기 위한 것. */}
        {vm.posterUrl && !firstFrameShown && (
          <img
            src={vm.posterUrl}
            alt=""
            aria-hidden
            className="pointer-events-none absolute inset-0 h-full w-full object-contain"
          />
        )}

        {/* 이름 */}
        {vm.petName && (
          <div className="pointer-events-none absolute inset-x-0 top-0 flex justify-center pt-[max(1.5rem,env(safe-area-inset-top))]">
            <p className="text-sm font-medium tracking-wide text-white/70">{vm.petName}</p>
          </div>
        )}

        {/* 하단 안내 — 더블탭은 재생 가능할 때만, 움직임 버튼은 iOS 미허용일 때만. */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-col items-center gap-3 pb-[max(2rem,env(safe-area-inset-bottom))]">
          {doubleTap?.available && (
            <p className="text-xs text-white/45">{t.doubleTapHint}</p>
          )}
          {gyroSupport === "ios-permission" && permission !== "granted" && !reducedMotion && (
            <button
              type="button"
              onClick={askMotion}
              className="pointer-events-auto rounded-full border border-white/20 px-4 py-2 text-xs text-white/70 active:bg-white/10"
            >
              {t.enableMotion}
            </button>
          )}
        </div>
      </div>
    </ShakerShell>
  );
}

/**
 * 전체 화면 껍데기 + 배경 레이어.
 *
 * 배경을 펫보다 **덜** 움직여 깊이를 만든다. 배경 자체는 무거운 자산을 쓰지 않는다 —
 * 공개 페이지는 QR 을 찍은 사람이 모바일 데이터로 여는 곳이라, BREATHING 영상
 * 하나만으로 충분히 무겁다.
 */
function ShakerShell({
  frame,
  animate,
  children,
}: {
  frame: ParallaxFrame;
  animate: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 overflow-hidden bg-[#07070a]">
      <div
        className="absolute inset-[-6%] bg-[radial-gradient(ellipse_at_50%_35%,rgba(120,110,150,0.20),transparent_62%)]"
        aria-hidden
        style={{
          transform: animate
            ? `translate3d(${frame.background.x.toFixed(2)}px, ${frame.background.y.toFixed(2)}px, 0)`
            : undefined,
          willChange: animate ? "transform" : undefined,
        }}
      />
      <div className="relative h-full w-full">{children}</div>
    </div>
  );
}
