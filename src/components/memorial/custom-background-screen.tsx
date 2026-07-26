"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Check, Flower2 } from "lucide-react";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { dataUrlToFile } from "@/lib/data-url-to-file";
import {
  enqueueCustomBackgroundJob,
  getCustomBackgroundJobStatus,
  type BackgroundVideoJobStatusResult,
} from "@/lib/background-bg-api";
import {
  CUSTOM_BG_JOB_ID_KEY,
  setStoredCustomBgVideoUrl,
} from "@/lib/custom-background-store";
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
      if (!uploadedImage) {
        setErrorMessage(t.missingPhoto);
        setState("failed");
        return;
      }
      setState("starting");
      setErrorMessage(null);
      try {
        const file = await toPhotoFile(uploadedImage);
        const contentId = getStoredContentId() || undefined;
        const job = await enqueueCustomBackgroundJob(file, { contentId });
        if (cancelledRef.current) return;
        localStorage.setItem(CUSTOM_BG_JOB_ID_KEY, job.job_id);
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
    <div className="h-full flex flex-col relative overflow-hidden">
      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative shrink-0">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{ background: "#1C1C1E", border: "1px solid #333333" }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>
        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light absolute left-1/2 -translate-x-1/2 text-center"
          style={{ color: "#F5F5F7" }}
        >
          {t.title}
        </motion.h1>
        <div className="w-10 h-10" />
      </header>

      <div className="flex-1 px-8 py-4 flex flex-col items-center justify-center text-center gap-6 min-h-0 overflow-y-auto">
        {(state === "starting" || state === "queued" || state === "running") && (
          <>
            <motion.div
              className="w-20 h-20 rounded-full flex items-center justify-center"
              style={{
                background: "linear-gradient(135deg, #c9a227, #f5d77a)",
                boxShadow: "0 0 40px rgba(201, 162, 39, 0.35)",
              }}
              animate={{ rotate: 360 }}
              transition={{ duration: 2.2, repeat: Infinity, ease: "linear" }}
            >
              <Flower2 className="w-9 h-9 text-[#0a0a0a]" strokeWidth={1.5} />
            </motion.div>
            <div>
              <p className="text-base font-light" style={{ color: "#F5F5F7" }}>
                {stageLabel || t.stageRunningDefault}
              </p>
              <p className="text-xs mt-3 tabular-nums" style={{ color: "#888" }}>
                {t.elapsedHint(elapsedSec)}
              </p>
              {showNoWorkerHint ? (
                <p className="text-xs mt-3 max-w-[260px] mx-auto" style={{ color: "#e8c97a" }}>
                  {t.noWorkerHint}
                </p>
              ) : null}
            </div>
          </>
        )}

        {state === "done" && resultVideoUrl && (
          <>
            <div className="w-full max-w-[280px] rounded-2xl overflow-hidden border border-white/10">
              <video
                src={resultVideoUrl}
                className="w-full aspect-video object-cover bg-black"
                autoPlay
                loop
                muted
                playsInline
              />
            </div>
            <div>
              <p className="text-base font-medium" style={{ color: "#F1E5D1" }}>
                {t.readyTitle}
              </p>
              <p className="text-xs mt-2 max-w-[260px] mx-auto" style={{ color: "#A1A1A6" }}>
                {t.readyBody}
              </p>
            </div>
            <motion.button
              type="button"
              onClick={() => onComplete(resultVideoUrl)}
              className="w-full max-w-[280px] py-3.5 rounded-2xl font-normal text-[15px] flex items-center justify-center gap-2"
              style={{
                background:
                  "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)",
                color: "#0a0a0a",
                boxShadow: "0 10px 40px rgba(201, 162, 39, 0.25)",
              }}
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
            <div
              className="w-full max-w-[300px] px-4 py-4 rounded-2xl text-sm"
              style={{
                background: "rgba(80, 20, 20, 0.35)",
                color: "#f5c2c2",
                border: "1px solid #553333",
              }}
            >
              <p className="font-medium mb-1">
                {state === "timeout" ? t.timeoutTitle : t.failedTitle}
              </p>
              <p className="text-xs" style={{ color: "#e0b8b8" }}>
                {state === "timeout" ? t.timeoutBody : errorMessage || t.startFailed}
              </p>
            </div>
            <div className="w-full max-w-[300px] flex flex-col gap-2">
              <button
                type="button"
                onClick={() => setRetryKey((k) => k + 1)}
                className="w-full py-3 rounded-xl text-sm font-medium"
                style={{
                  background: "rgba(201, 162, 39, 0.2)",
                  color: "#f5d77a",
                  border: "1px solid rgba(201, 162, 39, 0.35)",
                }}
              >
                {t.retry}
              </button>
              <button
                type="button"
                onClick={onBack}
                className="w-full py-3 rounded-xl text-sm"
                style={{ color: "#A1A1A6" }}
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
