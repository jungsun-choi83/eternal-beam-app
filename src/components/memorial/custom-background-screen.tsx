"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { Check, Flower2 } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { BackButton } from "@/components/ui/screen-header";
import { dataUrlToFile } from "@/lib/data-url-to-file";
import {
  enqueueCustomBackgroundJob,
  getCustomBackgroundJobStatus,
  type BackgroundVideoJobStatusResult,
} from "@/lib/background-bg-api";
import {
  CUSTOM_BG_CONTENT_ID_KEY,
  CUSTOM_BG_JOB_ID_KEY,
  setStoredCustomBgVideoUrl,
} from "@/lib/custom-background-store";
import { resolveCustomBackgroundResume } from "@/lib/custom-background-resume";
import { getStoredContentId } from "@/lib/persist-device-content";
import { useProcessingClock } from "@/lib/use-processing-clock";

const POLL_INTERVAL_MS = 5000;
// 로컬 워커가 큐를 안 보고 있을 때 사용자에게 알려주기까지의 시간(초) — 그래도
// polling은 계속함(워커가 늦게 켜질 수 있으므로 하드 실패는 아님).
const NO_WORKER_HINT_SEC = 60;
// 전체 대기 하드 타임아웃(초) — Luma 폴링 자체가 최대 20분(LUMA_POLL_MAX_SEC)까지
// 걸릴 수 있어 넉넉히 잡음. 이 이상 걸리면 폴링을 멈추고 사용자에게 알린다.
const HARD_TIMEOUT_SEC = 20 * 60;

type ScreenState = "starting" | "queued" | "running" | "done" | "failed" | "timeout";

interface CustomBackgroundScreenProps {
  uploadedImage: string | null;
  language?: string;
  onComplete: (videoUrl: string) => void;
  onBack: () => void;
}

async function toPhotoFile(uploadedImage: string): Promise<File> {
  if (uploadedImage.startsWith("data:")) {
    return dataUrlToFile(uploadedImage, "original.jpg");
  }
  const res = await fetch(uploadedImage);
  const blob = await res.blob();
  return new File([blob], "original.jpg", { type: blob.type || "image/jpeg" });
}

export function CustomBackgroundScreen({
  uploadedImage,
  language = "ko",
  onComplete,
  onBack,
}: CustomBackgroundScreenProps) {
  const t = memorialT(language).customBg;
  const [state, setState] = useState<ScreenState>("starting");
  const [stageLabel, setStageLabel] = useState<string>("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [resultVideoUrl, setResultVideoUrl] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  const active = state === "starting" || state === "queued" || state === "running";
  const { seconds: elapsedSec } = useProcessingClock(active);

  const cancelledRef = useRef(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // setTimeout 기반 polling 콜백에서도 최신 elapsedSec을 읽기 위한 미러 — 콜백은
  // 클로저라 state를 직접 못 읽으므로 ref로 우회.
  const elapsedSecRef = useRef(0);
  elapsedSecRef.current = elapsedSec;

  useEffect(() => {
    cancelledRef.current = false;

    const stopPolling = () => {
      if (pollTimerRef.current) {
        clearTimeout(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };

    const applyStatus = (job: BackgroundVideoJobStatusResult, elapsedAtPoll: number) => {
      if (cancelledRef.current) return;

      if (job.status === "done" && job.result_video_url) {
        setStoredCustomBgVideoUrl(job.result_video_url);
        setResultVideoUrl(job.result_video_url);
        setState("done");
        return;
      }
      if (job.status === "failed") {
        setErrorMessage(job.error || null);
        setState("failed");
        return;
      }

      const stage = job.progress?.stage;
      if (job.status === "queued") {
        setState("queued");
        setStageLabel(t.stageQueued);
      } else {
        setState("running");
        setStageLabel(
          (stage && t.stages[stage as keyof typeof t.stages]) || t.stageRunningDefault
        );
      }

      if (elapsedAtPoll >= HARD_TIMEOUT_SEC) {
        setState("timeout");
        return;
      }
      pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    };

    const poll = async () => {
      const jobId = localStorage.getItem(CUSTOM_BG_JOB_ID_KEY);
      if (!jobId || cancelledRef.current) return;
      try {
        const job = await getCustomBackgroundJobStatus(jobId);
        applyStatus(job, elapsedSecRef.current);
      } catch {
        // 네트워크 일시 오류 — 하드 타임아웃 전까지는 조용히 재시도.
        if (elapsedSecRef.current >= HARD_TIMEOUT_SEC) {
          if (!cancelledRef.current) setState("timeout");
          return;
        }
        pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    const start = async () => {
      const contentId = getStoredContentId() || null;

      // Phase 9 — 새로고침 재개. 같은 펫(content_id)의 서버 작업이 이미
      // 진행 중이면 새로 만들지 않고 그 작업의 상태를 이어서 조회한다.
      const decision = resolveCustomBackgroundResume(
        {
          jobId: localStorage.getItem(CUSTOM_BG_JOB_ID_KEY),
          contentId: localStorage.getItem(CUSTOM_BG_CONTENT_ID_KEY),
        },
        contentId
      );
      if (decision.action === "resume") {
        try {
          const job = await getCustomBackgroundJobStatus(decision.jobId);
          if (cancelledRef.current) return;
          if (job.status !== "failed") {
            applyStatus(job, elapsedSecRef.current);
            return;
          }
        } catch {
          // 서버에 더 이상 없거나 조회 실패 — 아래에서 새로 시작한다.
        }
      }

      // 재개하지 않기로 했거나 재개가 실패했다 — 남의/실패한 표식은 지운다.
      localStorage.removeItem(CUSTOM_BG_JOB_ID_KEY);
      localStorage.removeItem(CUSTOM_BG_CONTENT_ID_KEY);

      if (!uploadedImage) {
        setErrorMessage(t.missingPhoto);
        setState("failed");
        return;
      }
      setState("starting");
      setErrorMessage(null);
      try {
        const file = await toPhotoFile(uploadedImage);
        const job = await enqueueCustomBackgroundJob(file, { contentId: contentId || undefined });
        if (cancelledRef.current) return;
        localStorage.setItem(CUSTOM_BG_JOB_ID_KEY, job.job_id);
        if (contentId) localStorage.setItem(CUSTOM_BG_CONTENT_ID_KEY, contentId);
        setState("queued");
        setStageLabel(t.stageQueued);
        pollTimerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
      } catch (e) {
        if (cancelledRef.current) return;
        setErrorMessage(e instanceof Error ? e.message : t.startFailed);
        setState("failed");
      }
    };

    void start();

    return () => {
      cancelledRef.current = true;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uploadedImage, retryKey]);

  const showNoWorkerHint = state === "queued" && elapsedSec >= NO_WORKER_HINT_SEC;

  return (
    <div className="custom-bg-screen h-full flex flex-col relative overflow-hidden">
      <header className="eb-screen-header">
        <div className="eb-screen-header__leading">
          <BackButton onClick={onBack} label={memorialT(language).common.back} />
        </div>
        <h1 className="eb-screen-header__title eb-title text-center">{t.title}</h1>
        <div className="eb-screen-header__trailing" />
      </header>

      <div className="flex-1 px-6 py-4 flex flex-col items-center justify-center text-center gap-6 min-h-0 overflow-y-auto">
        {(state === "starting" || state === "queued" || state === "running") && (
          <>
            {/* Loading indicator — the one rotating element; gold wash, no glow. */}
            <motion.div
              className="w-20 h-20 rounded-full flex items-center justify-center"
              style={{
                background: "var(--eb-gold-wash)",
                border: "1px solid var(--eb-gold-line)",
                color: "var(--eb-gold-text)",
              }}
              animate={{ rotate: 360 }}
              transition={{ duration: 2.2, repeat: Infinity, ease: "linear" }}
              aria-hidden
            >
              <Flower2 className="w-9 h-9" strokeWidth={1.5} />
            </motion.div>
            <div role="status">
              <p className="eb-body font-medium">
                {stageLabel || t.stageRunningDefault}
              </p>
              <p className="eb-caption mt-3 tabular-nums">
                {t.elapsedHint(elapsedSec)}
              </p>
              {showNoWorkerHint ? (
                <p className="eb-notice eb-notice--warning mt-3 max-w-[280px] mx-auto text-left">
                  {t.noWorkerHint}
                </p>
              ) : null}
            </div>
          </>
        )}

        {state === "done" && resultVideoUrl && (
          <>
            <div className="theme-preview-frame w-full max-w-[280px] overflow-hidden">
              <video
                src={resultVideoUrl}
                className="w-full aspect-video object-cover"
                style={{ background: "var(--eb-surface-inverse)" }}
                autoPlay
                loop
                muted
                playsInline
              />
            </div>
            <div>
              <p className="eb-body font-medium">
                {t.readyTitle}
              </p>
              <p className="eb-body-sm mt-2 max-w-[280px] mx-auto">
                {t.readyBody}
              </p>
            </div>
            <motion.button
              type="button"
              onClick={() => onComplete(resultVideoUrl)}
              className="cta-gold eb-btn-label w-full max-w-[280px] flex items-center justify-center gap-2"
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
            >
              <Check className="w-4 h-4" />
              <span>{t.continueToPayment}</span>
            </motion.button>
          </>
        )}

        {(state === "failed" || state === "timeout") && (
          <>
            <div role="alert" className="eb-notice eb-notice--error w-full max-w-[300px] flex-col text-left">
              <p className="font-medium mb-1">
                {state === "timeout" ? t.timeoutTitle : t.failedTitle}
              </p>
              <p className="eb-caption" style={{ color: "inherit" }}>
                {state === "timeout" ? t.timeoutBody : errorMessage || t.startFailed}
              </p>
            </div>
            <div className="w-full max-w-[300px] flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setRetryKey((k) => k + 1)}
                className="eb-btn eb-btn--primary eb-btn--block"
              >
                {t.retry}
              </button>
              <button
                type="button"
                onClick={onBack}
                className="eb-btn eb-btn--ghost eb-btn--block"
              >
                {t.backToThemes}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
