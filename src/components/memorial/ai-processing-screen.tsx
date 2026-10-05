"use client";

import { useState, useEffect, useRef, memo, useMemo } from "react";
import { Check } from "lucide-react";
import { EternalBeamBrandMark } from "@/components/memorial/eternal-beam-brand-mark";
import { PrimaryButton } from "@/components/ui/buttons";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  assertUsableCutout,
  cutoutImage,
  isCutoutApiUnreachableError,
  isCutoutRejectedError,
  type CutoutResult,
} from "@/app/services/videoProcessingApi";
import { clientCutoutFromFile, dataUrlToFile } from "@/lib/client-cutout";
import { createDisplayImageUrl, createDisplayCutoutUrl } from "@/lib/display-image";
import {
  cutoutRejectionMessage,
  friendlyCutoutError,
  normalizeImageForCutout,
} from "@/lib/normalize-image";
import { mockCutoutFromFile } from "@/lib/mock-cutout";
import {
  clearServerCutoutSkipped,
  isServerCutoutSkipped,
  markServerCutoutDisabled,
} from "@/lib/server-cutout-available";
import { MOCK_CUTOUT_ENABLED } from "@/lib/test-app-flags";
import {
  CUTOUT_AUTO_REFINE,
  CUTOUT_SERVER_TIMEOUT_MS,
  CUTOUT_SPEED_MODE,
  CUTOUT_WARMUP_MAX_MS,
} from "@/lib/cutout-speed-mode";
import { warmupVideoApi } from "@/lib/video-api-warmup";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { isLiteUI } from "@/lib/ui-performance";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import { useProcessingClock } from "@/lib/use-processing-clock";
import { isClientCutoutFirst } from "@/lib/device-host-flags";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { PetPhoto } from "@/components/memorial/pet-photo";
import { setPendingCutout } from "@/lib/pending-generation";
import {
  persistPhase1Intake,
  buildPhase1IntakeReceipt,
  Phase1IntakeError,
  type ReadyIntakePair,
} from "@/lib/original-reference";
import { beginIntakePass, type IntakePass } from "@/lib/reference-sync";
import { isPhase1LockedError } from "@/lib/pet-input-lock";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { getPremiumAccessToken } from "@/lib/premium-auth-token";
import { syncEternalBeamIdentity } from "@/lib/supabase-auth";
import {
  requirePhase1Intake,
  type Phase1IntakeIdentity,
} from "@/lib/phase1-intake-session";
import { traceImage, dumpImageTrace } from "@/lib/image-trace"; // [IMAGE-TRACE]

export const ETERNAL_BEAM_PIPELINE_KEY = "eternal_beam_pipeline_v1";

const FILM_CONVERSION_SEC = Number(import.meta.env.VITE_FILM_CONVERSION_SEC ?? "0");
const CLIENT_CUTOUT_FALLBACK = import.meta.env.VITE_CLIENT_CUTOUT_FALLBACK !== "0";
/** 누끼 전/후 비교를 idle 단계 전에 유지 (ms) */
const COMPARE_HOLD_MS = Math.max(
  2000,
  Number(import.meta.env.VITE_COMPARE_HOLD_MS ?? "4500")
);
/** Step 1/2 main copy rotation interval */
const COPY_ROTATE_MS = 4500;
const COPY_FADE_MS = 500;
const HEADLINE_ROTATE_MS = 5500;

export interface StoredPipeline {
  content_id: string;
  cutout_display_url: string;
  dog_only_nobg_url: string;
  idle_video_url: string;
  action_video_url: string;
  /** COME_CLOSER (웹 전용 프리미엄 액션). 미생성 시 없음. */
  come_closer_video_url?: string | null;
  /**
   * 이 파이프라인의 영상들이 **배경을 이미 담고 있는가** (Phase 19).
   *
   * 없거나 false 면 레거시다 — 재생 쪽이 블랙키를 뽑고 테마 배경을 뒤에 깐다.
   * true 면 그 처리를 **전부 건너뛴다**(baked-playback.ts). 배경을 두 번
   * 적용하지 않기 위한 유일한 신호다.
   */
  background_baked?: boolean;
  /**
   * BREATH 자산의 명시적 전달 포맷 (Phase 7F).
   *
   * "packed_alpha" = vstack(상단 RGB·하단 알파 매트) 파생물 — 재생기가 packed
   * 렌더러를 **명시적으로** 선택한다(크로마 휴리스틱에 기대지 않는다).
   * 없으면 레거시 — background_baked 규칙(baked/blackkey)이 그대로 적용된다.
   */
  delivery_format?: string | null;
  /**
   * BREATH 를 만든 시스템 (Phase 7G).
   *
   * "phase7-run" = 새 Phase 1–7 실행 산출물. 이 값이 있으면 레거시
   * registry/register 쓰기를 하지 않는다 — 서버 발행(Phase 7A)이 pets 포인터의
   * 단일 저자이고, 클라이언트 등록이 그것을 덮으면 새 상태가 사라진다.
   */
  generation_source?: string | null;
  /**
   * 데이터베이스의 실제 QA 결정 (Phase 7G). "REVIEW" 면 발행되지 않은
   * 개발/현재-실행 재생이다 — PASS 로 가공하지 않는다.
   */
  qa_decision?: string | null;
  /** 이 영상들이 나온 정본 장면. 이후 행동 생성이 같은 장면을 재사용한다. */
  scene_id?: string | null;
  /** Phase 7B receipt: generation may start only from an intake-ready pair. */
  phase1_intake?: {
    status: "ready";
    pet_id: string;
    /** 하위 호환 단수 필드 — 준비된 **첫** 쌍. 기존 소비자는 이것만 읽는다. */
    original_reference_id: string;
    cutout_reference_id: string;
    /**
     * 활성 펫에서 준비 완료된 **모든** 원본/누끼 쌍(1–3장). 같은 인덱스끼리
     * 짝이다. 예전 세션에서 복원된 페이로드에는 없을 수 있어 optional 이다.
     */
    original_reference_ids?: string[];
    cutout_reference_ids?: string[];
  };
}

interface AIProcessingScreenProps {
  uploadedImage: string | null;
  uploadedImages?: string[];
  /**
   * 지금 처리 중인 **펫 자리**. 업로드 신원(content_id → pet_id)과 대기 누끼가
   * 이 이름 앞으로 보관된다. 예전에는 둘 다 앱 전체에 한 칸씩이라, 두 번째
   * 아이를 처리하면 첫 아이의 신원·누끼를 덮어썼다.
   */
  petSlotId: string;
  intakeIdentity?: Phase1IntakeIdentity | null;
  language?: string;
  onImageStateChange?: (
    index: number,
    state: { status: "pending" | "uploading" | "success" | "error"; error?: string | null },
  ) => void;
  onComplete: (cutoutUrl: string) => void;
}

const CLIENT_CUTOUT_FIRST = isClientCutoutFirst();

type ProcessingCopy = ReturnType<typeof memorialT>["processing"];

/**
 * subjectDetected:
 *  - true  → 서버가 지원 동물을 검출하고 품질 게이트를 통과시킴
 *  - undefined → 브라우저 WASM/목업 경로라 검증되지 않음 (검출기가 없음)
 * false 는 여기까지 오지 않는다 — 서버가 422로 거절하기 때문.
 */
type CutoutOutcome = {
  display: string;
  cutFile: File;
  contentId: string;
  subjectDetected?: boolean;
  /** 서버 누끼의 cutout_quality 메타 — 원본 레퍼런스 인테이크에 동봉한다. */
  quality?: unknown;
};

async function tryServerCutout(
  file: File,
  onStatus: (line: string) => void,
  t: ProcessingCopy,
  identity: Phase1IntakeIdentity,
  userId: string,
  accessToken: string,
): Promise<CutoutOutcome | null> {
  if (isServerCutoutSkipped()) return null;
  try {
    onStatus(t.serverWaking);
    await warmupVideoApi({ coldStart: true, maxWaitMs: CUTOUT_WARMUP_MAX_MS });
    onStatus(CUTOUT_SPEED_MODE ? t.serverCutoutFast : t.serverCutout);
    const cut = await cutoutImage(file, {
      userId,
      contentId: identity.contentId,
      saveToStorage: false,
      model: "isnet-general-use",
      autoRefine: CUTOUT_AUTO_REFINE,
      timeoutMs: CUTOUT_SERVER_TIMEOUT_MS,
      accessToken,
    });
    assertUsableCutout(cut);
    // 실제로 정제 패스가 돈 경우에만 안내 문구를 띄운다.
    // (예전에는 서버가 refined를 항상 true로 하드코딩해 항상 떴다.)
    if (cut.cutout_quality?.refined && cut.cutout_quality?.refinement_type) {
      onStatus(t.serverFurRefine);
    }
    return {
      display: cutoutDisplayUrl(cut),
      cutFile: await cutoutResultToFile(cut),
      contentId: cut.content_id,
      subjectDetected: cut.subject_detected !== false,
      quality: cut.cutout_quality ?? null,
    };
  } catch (e) {
    // 사진 자체가 거절된 경우(피사체 미검출 등)는 폴백으로 우회하지 않는다 —
    // 브라우저 WASM은 검출기가 없어서 "사람까지 포함된 누끼"를 만들어 낼 뿐이고,
    // 그게 그대로 유료 생성까지 흘러가는 것이 지금 고치려는 문제다.
    if (isCutoutRejectedError(e)) throw e;
    const msg = e instanceof Error ? e.message : String(e);
    if (isCutoutApiUnreachableError(msg)) {
      markServerCutoutDisabled();
    }
    return null;
  }
}

async function tryClientCutout(
  file: File,
  onStatus: (line: string) => void,
  t: ProcessingCopy,
  language: string,
  identity: Phase1IntakeIdentity,
): Promise<CutoutOutcome> {
  onStatus(CUTOUT_SPEED_MODE ? t.waitHintFast : t.waitHint);
  const display = await clientCutoutFromFile(file, onStatus, language);
  return {
    display,
    cutFile: dataUrlToFile(display, "cutout.png"),
    contentId: identity.contentId,
  };
}

async function runCutoutWithFallback(
  file: File,
  onStatus: (line: string) => void,
  t: ProcessingCopy,
  language: string,
  identity: Phase1IntakeIdentity,
  userId: string,
  accessToken: string,
): Promise<CutoutOutcome> {
  if (MOCK_CUTOUT_ENABLED) {
    onStatus(t.mockCutout);
    const display = await mockCutoutFromFile(file);
    return {
      display,
      cutFile: dataUrlToFile(display, "cutout.jpg"),
      contentId: identity.contentId,
    };
  }

  // 기본: Render 서버 누끼(1회·90초). 실패 시 폰 WASM(768px, 1~3분).
  if (!CLIENT_CUTOUT_FIRST) {
    const server = await tryServerCutout(file, onStatus, t, identity, userId, accessToken);
    if (server) return server;

    if (!CLIENT_CUTOUT_FALLBACK) {
      throw new Error(t.serverOnlyFailed);
    }
    onStatus(t.serverThenClient);
    return tryClientCutout(file, onStatus, t, language, identity);
  }

  onStatus(t.clientCutout);
  try {
    return await tryClientCutout(file, onStatus, t, language, identity);
  } catch (clientErr) {
    if (!CLIENT_CUTOUT_FALLBACK) {
      throw clientErr;
    }
    onStatus(t.clientThenServer);
    const server = await tryServerCutout(file, onStatus, t, identity, userId, accessToken);
    if (server) return server;
    throw clientErr;
  }
}

function cutoutDisplayUrl(result: CutoutResult): string {
  if (result.cutout_url) return result.cutout_url;
  if (result.cutout_png_base64)
    return `data:image/png;base64,${result.cutout_png_base64}`;
  return "";
}

function isCutoutMemoryError(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("bad alloc") ||
    m.includes("allocation") ||
    m.includes("out of memory") ||
    m.includes("onnxruntimeerror") ||
    m.includes("메모리")
  );
}

async function cutoutResultToFile(result: CutoutResult): Promise<File> {
  if (result.cutout_url) {
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 120_000);
    let r: Response;
    try {
      r = await fetch(result.cutout_url, { signal: ctrl.signal });
    } finally {
      clearTimeout(tid);
    }
    if (!r.ok) throw new Error(`누끼 이미지를 불러오지 못했습니다 (${r.status}).`);
    const blob = await r.blob();
    const t = blob.type?.startsWith("image/") ? blob.type : "image/png";
    return new File([blob], "cutout.png", { type: t });
  }
  if (result.cutout_png_base64) {
    const bytes = Uint8Array.from(atob(result.cutout_png_base64), (c) =>
      c.charCodeAt(0)
    );
    return new File([bytes], "cutout.png", { type: "image/png" });
  }
  throw new Error("No cutout image in response");
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

async function buildIdentityProfile(petId: string, accessToken: string): Promise<void> {
  const res = await fetch(`${apiBase()}/api/v1/pet/identity/${encodeURIComponent(petId)}/build`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({}),
  });
  if (!res.ok) {
    let message = `신원 프로필 빌드에 실패했습니다 (${res.status}).`;
    try {
      const body = (await res.json()) as { detail?: { message?: string } };
      const detail = body?.detail?.message?.trim();
      if (detail) message = detail;
    } catch {
      /* keep default */
    }
    throw new Error(message);
  }
}

async function runFilmConversionDemo(
  onTick: (pct: number, line: string) => void,
  t: ProcessingCopy
): Promise<void> {
  if (FILM_CONVERSION_SEC <= 0) {
    onTick(92, t.lumaSkip);
    return;
  }
  const steps = Math.max(3, Math.min(12, FILM_CONVERSION_SEC));
  for (let i = 0; i < steps; i++) {
    onTick(40 + Math.round(((i + 1) / steps) * 55), t.convertingHint);
    await sleep((FILM_CONVERSION_SEC * 1000) / steps);
  }
}

const CompareImages = memo(function CompareImages({
  original,
  cutout,
  beforeLabel,
  afterLabel,
  showCheck,
}: {
  original: string;
  cutout: string;
  beforeLabel: string;
  afterLabel: string;
  showCheck: boolean;
}) {
  return (
    <div className="ai-processing-screen__compare">
      <div className="ai-processing-screen__compare-grid">
        <div className="compare-panel">
          <p className="compare-panel__label">{beforeLabel}</p>
          {/* 사용자 원본 사진 — 전체가 보이게(PetPhoto). 예전 object-cover 는
              세로 사진의 위아래를 잘라 냈다. */}
          <div className="aspect-square relative overflow-hidden">
            <PetPhoto src={original} alt={beforeLabel} variant="full" className="absolute inset-0" />
          </div>
        </div>
        <div className="compare-panel compare-panel--cutout">
          <p className="compare-panel__label">{afterLabel}</p>
          <CutoutStage plain className="aspect-square relative w-full min-h-[120px]">
            <img
              src={cutout}
              alt={afterLabel}
              className="cutout-stage__subject w-full h-full"
              decoding="async"
            />
            {showCheck ? (
              <div className="eb-check-mark absolute bottom-2 right-2 z-10" aria-hidden>
                <Check className="w-3.5 h-3.5" strokeWidth={3} />
              </div>
            ) : null}
          </CutoutStage>
        </div>
      </div>
    </div>
  );
});

export function AIProcessingScreen({
  uploadedImage,
  uploadedImages,
  petSlotId,
  intakeIdentity,
  language = "ko",
  onImageStateChange,
  onComplete,
}: AIProcessingScreenProps) {
  const m = memorialT(language);
  const t = m.processing;
  const lite = isLiteUI();

  const [currentStep, setCurrentStep] = useState(0);
  const [processingActive, setProcessingActive] = useState(false);
  const { seconds: elapsedSec } = useProcessingClock(processingActive);
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState("");
  const [displayOriginal, setDisplayOriginal] = useState<string | null>(null);
  const [cutoutPreview, setCutoutPreview] = useState<string | null>(null);
  const [showCompare, setShowCompare] = useState(false);
  const [idlePreviewUrl, setIdlePreviewUrl] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  /** 사진 집합 동기화가 건너뛰어졌거나 실패했을 때의 **비차단** 알림. */
  const [syncNotice, setSyncNotice] = useState<string | null>(null);
  const [step1Index, setStep1Index] = useState(0);
  const [step1Fading, setStep1Fading] = useState(false);
  const [step2Index, setStep2Index] = useState(0);
  const [step2Fading, setStep2Fading] = useState(false);
  const [headlineIndex, setHeadlineIndex] = useState(0);
  const [headlineFading, setHeadlineFading] = useState(false);

  const runTokenRef = useRef(0);
  const onCompleteRef = useRef(onComplete);
  const onImageStateChangeRef = useRef(onImageStateChange);
  onCompleteRef.current = onComplete;
  onImageStateChangeRef.current = onImageStateChange;

  const intakeImages = useMemo(
    () =>
      uploadedImages && uploadedImages.length > 0
        ? uploadedImages.filter((url) => url.startsWith("data:image/"))
        : uploadedImage && uploadedImage.startsWith("data:image/")
          ? [uploadedImage]
          : [],
    [uploadedImages, uploadedImage],
  );

  /**
   * 지금 UI 에 있는 **모든** 사진 — 업로드 대상만 추린 intakeImages 가 아니다.
   * 서버 동기화는 이 목록에 없는 원본을 물리므로, 한 장이라도 빠지면 안 된다.
   */
  const currentPhotos = useMemo(
    () =>
      uploadedImages && uploadedImages.length > 0
        ? uploadedImages
        : uploadedImage
          ? [uploadedImage]
          : [],
    [uploadedImages, uploadedImage],
  );
  const currentPhotosRef = useRef(currentPhotos);
  currentPhotosRef.current = currentPhotos;

  useEffect(() => {
    if (intakeImages.length === 0) return;
    const firstImage = intakeImages[0];
    if (!firstImage) return;
    let cancelled = false;
    createDisplayImageUrl(firstImage, 480).then((url) => {
      if (!cancelled) setDisplayOriginal(url);
    });
    return () => {
      cancelled = true;
    };
  }, [intakeImages]);

  const step1Lines =
    t.statusLines.length > 0 ? t.statusLines : [t.step1Main];

  useEffect(() => {
    if (!processingActive || error) {
      setHeadlineIndex(0);
      setHeadlineFading(false);
      return;
    }

    const titles = t.titles.length > 0 ? t.titles : [t.headline];
    if (titles.length <= 1) return;

    setHeadlineIndex(0);
    setHeadlineFading(false);

    const interval = window.setInterval(() => {
      setHeadlineFading(true);
      window.setTimeout(() => {
        setHeadlineIndex((i) => (i + 1) % titles.length);
        setHeadlineFading(false);
      }, COPY_FADE_MS);
    }, HEADLINE_ROTATE_MS);

    return () => window.clearInterval(interval);
  }, [processingActive, error, language]);

  useEffect(() => {
    if (currentStep !== 0) {
      setStep1Index(0);
      setStep1Fading(false);
      return;
    }

    const lines = t.statusLines.length > 0 ? t.statusLines : [t.step1Main];
    setStep1Index(0);
    setStep1Fading(false);

    if (lines.length <= 1) return;

    const interval = window.setInterval(() => {
      setStep1Fading(true);
      window.setTimeout(() => {
        setStep1Index((i) => (i + 1) % lines.length);
        setStep1Fading(false);
      }, COPY_FADE_MS);
    }, COPY_ROTATE_MS);

    return () => window.clearInterval(interval);
  }, [currentStep, language]);

  useEffect(() => {
    if (currentStep !== 1) {
      setStep2Index(0);
      setStep2Fading(false);
      return;
    }

    const rotateCount = t.step2Rotate.length;
    setStep2Index(0);
    setStep2Fading(false);

    if (rotateCount <= 1) return;

    const interval = window.setInterval(() => {
      setStep2Fading(true);
      window.setTimeout(() => {
        setStep2Index((i) => (i + 1) % rotateCount);
        setStep2Fading(false);
      }, COPY_FADE_MS);
    }, COPY_ROTATE_MS);

    return () => window.clearInterval(interval);
  }, [currentStep, language]);

  const headlineCopy =
    t.titles.length > 0
      ? t.titles[headlineIndex] ?? t.headline
      : t.headline;

  const mainCopy =
    currentStep === 0
      ? step1Lines[step1Index] ?? t.step1Main
      : currentStep === 1
        ? t.step2Rotate[step2Index] ?? t.step2Rotate[0]
        : currentStep === 2
          ? t.step3Main
          : "";

  const copyFading =
    (currentStep === 0 && step1Fading) || (currentStep === 1 && step2Fading);

  useEffect(() => {
    if (intakeImages.length === 0) return;

    const myToken = ++runTokenRef.current;
    let cancelled = false;

    const fail = (msg: string) => {
      if (cancelled || myToken !== runTokenRef.current) return;
      setError(friendlyCutoutError(msg, language));
    };

    (async () => {
      // 이 펫의 처리 패스. 끝나면(성공·실패·취소 모두) 반드시 닫는다 — 닫지 않으면
      // 같은 펫의 다음 패스가 시작하지 못한다.
      let intakePass: IntakePass | null = null;
      setProcessingActive(true);
      await new Promise((r) => setTimeout(r, 50));
      setError(null);
      setSyncNotice(null);
      setCutoutPreview(null);
      setShowCompare(false);
      setIdlePreviewUrl(null);
      setCurrentStep(0);
        setStatusLine(t.uploading);

      try {
        setStatusLine(t.steps[0].description);
        const stableIdentity = requirePhase1Intake(petSlotId, intakeIdentity);
        const auth = await getPremiumAccessToken();
        if (!auth.token) throw new Error("로그인 세션을 확인하지 못했습니다. 다시 로그인해 주세요.");
        const userId =
          auth.source === "supabase"
            ? await syncEternalBeamIdentity()
            : getEternalBeamUserId();
        if (!userId) throw new Error("인증된 사용자 신원을 확인하지 못했습니다.");

        let firstReady: {
          display: string;
          cutFile: File;
          contentId: string;
          petId: string;
          referenceId: string;
          cutoutReferenceId: string;
        } | null = null;
        /** 활성 펫에서 준비된 **모든** 쌍 — 세션 영수증의 실제 내용이다. */
        const readyPairs: ReadyIntakePair[] = [];
        let successCount = 0;
        let failedCount = 0;
        const total = intakeImages.length;

        // 업로드 **전에** 대장을 지금의 사진 집합에 맞춘다: 빠지거나 바뀐 사진이
        // 물러나 자리가 비므로, 교체한 사진이 같은 패스에서 409 없이 올라간다.
        // 같은 펫의 앞선 패스가 아직 돌고 있으면 그것이 끝날 때까지 기다린다.
        // 동기화 실패는 패스를 막지 않는다 — 알림만 띄운다.
        intakePass = await beginIntakePass({
          petId: stableIdentity.petId,
          photos: currentPhotosRef.current,
          accessToken: auth.token,
          isCurrent: () => !cancelled && myToken === runTokenRef.current,
          onNotice: (notice) => {
            if (cancelled || myToken !== runTokenRef.current) return;
            setSyncNotice(notice.kind === "hash_failed" ? t.syncSkippedNotice : t.syncFailedNotice);
          },
        });
        if (!intakePass) return; // 기다리는 사이 이 패스가 낡았다
        // 생성이 이미 시작된 펫이다 — 사진을 더 올리지 않고 그 사실을 알려 준다.
        if (intakePass.locked) throw new Error(t.photosLocked);
        const preSyncOk = intakePass.preSyncOk;

        for (let index = 0; index < total; index += 1) {
          const sourceImage = intakeImages[index];
          if (!sourceImage) continue;

          try {
            onImageStateChangeRef.current?.(index, { status: "uploading", error: null });
            setStatusLine(`${t.uploadingImage(index + 1, total)}`);

            const original = await persistPhase1Intake({
              userId,
              contentId: stableIdentity.contentId,
              dataUrl: sourceImage,
              accessToken: auth.token,
            });
            if (
              !original.recorded ||
              !original.referenceId ||
              original.userId !== userId ||
              original.contentId !== stableIdentity.contentId ||
              original.petId !== stableIdentity.petId
            ) {
              throw new Error("원본 사진의 Phase 1 저장 결과가 업로드 신원과 일치하지 않습니다.");
            }

            await traceImage(`pipeline:uploadedImage:${index + 1}`, sourceImage, "original-upload");
            const file = await normalizeImageForCutout(sourceImage);
            const cutout = await runCutoutWithFallback(
              file,
              (line) => {
                if (!cancelled && myToken === runTokenRef.current) {
                  setStatusLine(`${t.processingImage(index + 1, total)} · ${line}`);
                }
              },
              t,
              language,
              stableIdentity,
              userId,
              auth.token,
            );

            const contentId = cutout.contentId;
            if (contentId !== stableIdentity.contentId) {
              throw new Error("누끼 결과의 업로드 식별자가 원본과 일치하지 않습니다.");
            }

            const ready = await persistPhase1Intake({
              userId,
              contentId,
              dataUrl: sourceImage,
              accessToken: auth.token,
              cutoutFile: cutout.cutFile,
              diagnostics: cutout.quality ?? undefined,
            });
            if (
              !ready.intakeReady ||
              !ready.referenceId ||
              !ready.cutoutReferenceId ||
              ready.referenceId !== original.referenceId ||
              ready.petId !== stableIdentity.petId
            ) {
              throw new Error("Phase 1 원본과 누끼의 연결을 확인하지 못했습니다.");
            }

            await traceImage(`cutout:result-file:${index + 1}`, cutout.cutFile, "cutout-result");
            await traceImage(`cutout:display-url:${index + 1}`, cutout.display, "cutout-result");

            successCount += 1;
            readyPairs.push({
              petId: ready.petId,
              referenceId: ready.referenceId,
              cutoutReferenceId: ready.cutoutReferenceId,
            });
            onImageStateChangeRef.current?.(index, { status: "success", error: null });

            if (!firstReady) {
              firstReady = {
                display: cutout.display,
                cutFile: cutout.cutFile,
                contentId,
                petId: ready.petId,
                referenceId: ready.referenceId,
                cutoutReferenceId: ready.cutoutReferenceId,
              };

              const cutThumb = await createDisplayCutoutUrl(cutout.display, 480);
              if (cancelled || myToken !== runTokenRef.current) return;
              setCutoutPreview(cutThumb);
              setShowCompare(true);
            }
          } catch (imageError) {
            let msg = imageError instanceof Error ? imageError.message : String(imageError);
            if (
              imageError instanceof Phase1IntakeError &&
              imageError.code === "PHASE1_ORIGINAL_LIMIT"
            ) {
              // 자리가 없다는 뜻이다. 사전 동기화가 실패했다면 뺀 사진이 아직
              // 자리를 쥐고 있는 것이므로, 무엇을 하면 되는지 알려 준다.
              msg = preSyncOk ? t.originalLimitReached : t.originalLimitAfterSyncFailure;
            } else if (isPhase1LockedError(imageError)) {
              // 다른 탭이나 예전 앱이 그 사이 생성을 시작했다.
              msg = t.photosLocked;
            }
            onImageStateChangeRef.current?.(index, { status: "error", error: msg });
            failedCount += 1;
          }

          if (cancelled || myToken !== runTokenRef.current) return;
        }

        // 루프 **뒤에** 같은 전체 목록으로 한 번 더 — 멱등한 안전망이다. 이번
        // 패스에서 업로드가 실패한 사진도 목록에 있으므로 예전 행은 살아남는다.
        await intakePass.finish({ syncAfter: true });

        dumpImageTrace();

        if (!firstReady || successCount === 0) {
          throw new Error(t.allUploadsFailed);
        }
        // 영수증은 첫 장이 아니라 **활성 펫의 준비된 전부**다. 다른 펫 슬롯의
        // 레퍼런스는 buildPhase1IntakeReceipt 가 pet_id 로 걸러 낸다.
        const intakeReceipt = buildPhase1IntakeReceipt(firstReady.petId, readyPairs);
        if (!intakeReceipt) {
          throw new Error(t.allUploadsFailed);
        }
        const hasFailures = failedCount > 0 || successCount !== total;
        if (hasFailures) {
          setStatusLine(t.someUploadsFailed(failedCount, total));
        }

        setStatusLine(t.cutoutDone);
        await sleep(COMPARE_HOLD_MS);
        if (cancelled || myToken !== runTokenRef.current) return;

        // accepted 레퍼런스(원본+파생)가 모두 기록된 뒤, 같은 pet 에 대해
        // 신원 프로필을 1회 빌드한다.
        setStatusLine(
          hasFailures
            ? `${t.someUploadsFailed(failedCount, total)} ${t.buildingIdentity}`
            : t.buildingIdentity,
        );
        await buildIdentityProfile(firstReady.petId, auth.token);
        if (cancelled || myToken !== runTokenRef.current) return;

        setPendingCutout(
          firstReady.cutFile,
          firstReady.contentId,
          firstReady.display,
          petSlotId,
        );

        const stored: StoredPipeline = {
          content_id: firstReady.contentId,
          cutout_display_url: firstReady.display,
          dog_only_nobg_url: firstReady.display,
          idle_video_url: "", // 아직 생성 전 — 확인 후에 채워진다
          action_video_url: "",
          phase1_intake: intakeReceipt,
        };
        try {
          sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(stored));
          localStorage.setItem("eternal_beam_content_id", stored.content_id);
          localStorage.setItem("eternal_beam_current_content_id", stored.content_id);
        } catch {
          /* ignore */
        }

        setCurrentStep(2);
        setStatusLine(t.done);

        setTimeout(() => {
          if (cancelled || myToken !== runTokenRef.current) return;
          onCompleteRef.current(firstReady.display);
        }, lite ? 300 : 500);
      } catch (e) {
        if (isCutoutRejectedError(e)) {
          // 사진이 거절된 경우는 서버 장애 문구("깨어나는 중…")로 뭉뚱그리지 않고
          // 무엇이 문제인지 그대로 알려 준다.
          fail(cutoutRejectionMessage(e.code, language) ?? e.message);
        } else {
          const msg =
            e instanceof Error ? e.message : typeof e === "string" ? e : "Processing failed";
          fail(msg);
        }
      } finally {
        // 취소·예외로 루프를 빠져나온 경우에도 패스를 닫는다 (이미 닫혔으면 무동작).
        await intakePass?.finish({ syncAfter: false });
        if (!cancelled && myToken === runTokenRef.current) {
          setProcessingActive(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      setProcessingActive(false);
    };
  }, [intakeImages, intakeIdentity, petSlotId, language, retryKey]);

  const originalForUi = displayOriginal || uploadedImage;
  const showComparePanel =
    currentStep === 0 && showCompare && originalForUi && cutoutPreview;
  const showIdlePreview =
    currentStep >= 1 && Boolean(idlePreviewUrl || cutoutPreview);

  return (
    <div className="ai-processing-screen">
      <header className="ai-processing-screen__header">
        <h1 className="ai-processing-screen__title">{t.title}</h1>
        <p className="ai-processing-screen__supporting-copy">{t.supportingCopy}</p>
      </header>

      <div className="ai-processing-screen__body">
        <div className="ai-processing-screen__panel eb-fade-up">
          <div className="ai-processing-screen__visual">
            {showComparePanel ? (
              <CompareImages
                original={originalForUi}
                cutout={cutoutPreview}
                beforeLabel={t.before}
                afterLabel={t.after}
                showCheck={false}
              />
            ) : showIdlePreview ? (
              <div className="ai-processing-screen__idle-preview">
                <PetIdleDisplay
                  idleVideoUrl={idlePreviewUrl}
                  cutoutUrl={cutoutPreview}
                  // 생성 전 화면 — 데모 mp4 로 채우지 않는다(정적 누끼만).
                  allowDemoFallback={false}
                  // **명시적으로** false 다 (Phase 25). 이 화면은 생성 이전 단계라
                  // idlePreviewUrl 이 채워지는 경로가 없고, 나가는 것은 언제나 정적
                  // 누끼다 — 구운 장면이 여기로 올 수 없다. 기본값에 기대지 않고
                  // 적어 두는 이유는, 빠뜨린 것과 그렇게 정한 것을 구분하기 위해서다.
                  backgroundBaked={false}
                  className="ai-processing-screen__idle-pet w-full h-full object-contain"
                />
              </div>
            ) : originalForUi && currentStep === 0 ? (
              <div className="ai-processing-screen__source-preview">
                <img src={originalForUi} alt="" decoding="async" />
              </div>
            ) : null}

            {currentStep === 1 && !showIdlePreview && !lite ? (
              <div className="processing-scanline ai-processing-screen__scanline" aria-hidden />
            ) : null}
          </div>

          <div className="ai-processing-screen__details" aria-live="polite">
            <div className="ai-processing-screen__headline-row">
              <div>
                <StatusBadge tone={error ? "error" : currentStep >= 2 ? "success" : "loading"}>
                  {error ? t.errorBadge : currentStep >= 2 ? t.done : t.statusPreparing}
                </StatusBadge>
                <p
                  className={`ai-processing-screen__headline processing-copy-fade ${
                    headlineFading ? "processing-copy-fade--out" : ""
                  }`}
                >
                  {headlineCopy}
                </p>
              </div>
              {!error ? (
                <p className="ai-processing-screen__clock">
                  <span>{m.generationProgress.elapsedLabel}</span>
                  <strong>
                    {Math.floor(elapsedSec / 60)}:{String(elapsedSec % 60).padStart(2, "0")}
                  </strong>
                </p>
              ) : null}
            </div>

            <div className="ai-processing-screen__activity">
              <span className="ai-processing-screen__activity-dot" aria-hidden />
              <div>
                <p
                  className={`ai-processing-screen__copy processing-copy-fade ${
                    copyFading ? "processing-copy-fade--out" : ""
                  }`}
                >
                  {mainCopy}
                </p>
                {currentStep === 1 ? (
                  <p className="ai-processing-screen__status">{t.step2Sub}</p>
                ) : statusLine && currentStep === 0 ? (
                  <p className="ai-processing-screen__status">{statusLine}</p>
                ) : null}
              </div>
            </div>

            {!error && currentStep === 0 && elapsedSec >= 3 ? (
              <p className="ai-processing-screen__hint">{t.waitHint}</p>
            ) : null}

            {syncNotice && !error ? (
              <div className="eb-notice eb-notice--warning" role="status">
                {syncNotice}
              </div>
            ) : null}

            {error ? (
              <div className="ai-processing-screen__error">
                <div className="eb-notice eb-notice--error" role="alert">
                  {error}
                </div>
                <PrimaryButton
                  block
                  onClick={() => {
                    setError(null);
                    clearServerCutoutSkipped();
                    setRetryKey((k) => k + 1);
                  }}
                >
                  {t.retry}
                </PrimaryButton>
              </div>
            ) : null}

            <EternalBeamBrandMark language={language} />
          </div>
        </div>
      </div>
    </div>
  );
}
