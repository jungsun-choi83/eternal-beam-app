"use client";

import { useState, useEffect, useRef, memo } from "react";
import { Sparkles, Check } from "lucide-react";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import {
  cutoutImage,
  generatePetVideo,
  isCutoutApiUnreachableError,
  type CutoutResult,
} from "@/app/services/videoProcessingApi";
import { clientCutoutFromFile } from "@/lib/client-cutout";
import { createDisplayImageUrl, createDisplayCutoutUrl } from "@/lib/display-image";
import { friendlyCutoutError, normalizeImageToJpegFile } from "@/lib/normalize-image";
import { mockCutoutFromFile } from "@/lib/mock-cutout";
import { markServerCutoutDisabled } from "@/lib/server-cutout-available";
import { MOCK_CUTOUT_ENABLED } from "@/lib/test-app-flags";
import { warmupVideoApi } from "@/lib/video-api-warmup";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { isLiteUI } from "@/lib/ui-performance";
import { useProcessingClock } from "@/lib/use-processing-clock";

export const ETERNAL_BEAM_PIPELINE_KEY = "eternal_beam_pipeline_v1";

const LUMA_ENABLED = import.meta.env.VITE_ENABLE_LUMA === "1";
const FILM_CONVERSION_SEC = Number(import.meta.env.VITE_FILM_CONVERSION_SEC || "12");

export interface StoredPipeline {
  content_id: string;
  cutout_display_url: string;
  dog_only_nobg_url: string;
  idle_video_url: string;
  action_video_url: string;
}

interface AIProcessingScreenProps {
  uploadedImage: string | null;
  language?: string;
  onComplete: (cutoutUrl: string) => void;
}

const CLIENT_CUTOUT_FIRST = import.meta.env.VITE_CLIENT_CUTOUT === "1";

type ProcessingCopy = ReturnType<typeof memorialT>["processing"];

async function runCutoutWithFallback(
  file: File,
  onStatus: (line: string) => void,
  t: ProcessingCopy,
  language: string
): Promise<{ display: string; cutFile: File; contentId: string }> {
  if (MOCK_CUTOUT_ENABLED) {
    onStatus(t.mockCutout);
    const display = await mockCutoutFromFile(file);
    return {
      display,
      cutFile: dataUrlToFile(display, "cutout.jpg"),
      contentId: `mock_${Date.now()}`,
    };
  }

  // 기본: Render 서버 누끼 (폰에서 AI 모델 다운로드 방지). VITE_CLIENT_CUTOUT=1 이면 기기만.
  if (!CLIENT_CUTOUT_FIRST) {
    const serverAttempts = 2;
    for (let attempt = 0; attempt < serverAttempts; attempt++) {
      try {
        if (attempt === 0) {
          onStatus(t.serverWaking);
          await warmupVideoApi({ retries: 3, timeoutMs: 15_000 });
        } else {
          onStatus(t.serverRetry);
          await sleep(2000);
          await warmupVideoApi({ retries: 2, timeoutMs: 20_000 });
        }
        onStatus(t.serverCutout);
        const cut = await cutoutImage(file, {
          userId: "anonymous",
          saveToStorage: false,
          model: "isnet-general-use",
        });
        const display = cutoutDisplayUrl(cut);
        if (display && !cut.error) {
          return {
            display,
            cutFile: await cutoutResultToFile(cut),
            contentId: cut.content_id || `srv_${Date.now()}`,
          };
        }
        if (cut.error) {
          onStatus(
            isCutoutMemoryError(cut.error)
              ? t.serverThenClient
              : `${t.serverThenClient} (${cut.error})`
          );
        }
        break;
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        if (isCutoutApiUnreachableError(msg)) {
          markServerCutoutDisabled();
        }
        if (attempt < serverAttempts - 1) continue;
        onStatus(t.clientCutout);
      }
    }
  } else {
    onStatus(t.clientCutout);
  }

  onStatus(t.waitHint);
  const display = await clientCutoutFromFile(file, onStatus, language);
  return {
    display,
    cutFile: dataUrlToFile(display, "cutout.png"),
    contentId: `client_${Date.now()}`,
  };
}

function dataUrlToFile(dataUrl: string, filename: string): File {
  const arr = dataUrl.split(",");
  const mime = arr[0].match(/:(.*?);/)?.[1] || "image/jpeg";
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8arr = new Uint8Array(n);
  while (n--) u8arr[n] = bstr.charCodeAt(0);
  return new File([u8arr], filename, { type: mime });
}

function cutoutDisplayUrl(result: CutoutResult): string {
  if (result.cutout_url) return result.cutout_url;
  if (result.cutout_png_base64)
    return `data:image/png;base64,${result.cutout_png_base64}`;
  return "";
}

function isSkippableLumaError(message: string): boolean {
  const m = message.toLowerCase();
  return (
    m.includes("insufficient credit") ||
    m.includes("크레딧") ||
    m.includes("not found") ||
    m.includes("404") ||
    m.includes("luma_api_key") ||
    m.includes("luma api") ||
    m.includes("generate-pet-video")
  );
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

async function runFilmConversionDemo(
  onTick: (pct: number, line: string) => void,
  t: ProcessingCopy
): Promise<void> {
  const steps = Math.max(6, Math.min(20, FILM_CONVERSION_SEC));
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
    <div className="w-full max-w-[320px] mb-5 relative z-10">
      <div className="grid grid-cols-2 gap-3">
        <div className="compare-panel">
          <p className="compare-panel__label">{beforeLabel}</p>
          <div className="aspect-square relative overflow-hidden">
            <img
              src={original}
              alt={beforeLabel}
              className="absolute inset-0 w-full h-full object-cover"
              decoding="async"
            />
          </div>
        </div>
        <div className="compare-panel compare-panel--cutout">
          <p className="compare-panel__label">{afterLabel}</p>
          <CutoutStage className="aspect-square relative">
            <img
              src={cutout}
              alt={afterLabel}
              className="cutout-stage__subject"
              decoding="async"
            />
            {showCheck ? (
              <div
                className="absolute bottom-2 right-2 z-10 w-6 h-6 rounded-full flex items-center justify-center"
                style={{
                  background: "linear-gradient(135deg, #c9a227, #e8d5a3)",
                }}
              >
                <Check className="w-3.5 h-3.5 text-[#0a0a0a]" strokeWidth={3} />
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
  language = "ko",
  onComplete,
}: AIProcessingScreenProps) {
  const m = memorialT(language);
  const t = m.processing;
  const lite = isLiteUI();

  const [currentStep, setCurrentStep] = useState(0);
  const [progress, setProgress] = useState(10);
  const [processingActive, setProcessingActive] = useState(false);
  const { seconds: elapsedSec, tick: clockTick } = useProcessingClock(processingActive);
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState("");
  const [displayOriginal, setDisplayOriginal] = useState<string | null>(null);
  const [cutoutPreview, setCutoutPreview] = useState<string | null>(null);
  const [showCompare, setShowCompare] = useState(false);

  const runTokenRef = useRef(0);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    if (!uploadedImage) return;
    let cancelled = false;
    createDisplayImageUrl(uploadedImage, 480).then((url) => {
      if (!cancelled) setDisplayOriginal(url);
    });
    return () => {
      cancelled = true;
    };
  }, [uploadedImage]);

  const titles = t.titles;
  const statusLines = t.statusLines;
  const titleIndex = titles.length ? clockTick % titles.length : 0;
  const statusLineIndex = statusLines.length ? Math.floor(clockTick / 3) % statusLines.length : 0;
  const rotatingStatus = statusLines[statusLineIndex] ?? t.steps[0].description;

  useEffect(() => {
    if (!uploadedImage) return;

    const myToken = ++runTokenRef.current;
    let cancelled = false;

    const fail = (msg: string) => {
      if (cancelled || myToken !== runTokenRef.current) return;
      setError(friendlyCutoutError(msg, language));
      setProgress(0);
    };

    (async () => {
      setProcessingActive(true);
      await new Promise((r) => setTimeout(r, 50));
      setError(null);
      setCutoutPreview(null);
      setShowCompare(false);
      setCurrentStep(0);
      setProgress(10);
      setStatusLine(t.uploading);

      try {
        setStatusLine(t.steps[0].description);
        const file = await normalizeImageToJpegFile(uploadedImage);

        const { display, cutFile, contentId: cutContentId } = await runCutoutWithFallback(
          file,
          (line) => {
            if (!cancelled && myToken === runTokenRef.current) setStatusLine(line);
          },
          t,
          language
        );

        if (cancelled || myToken !== runTokenRef.current) return;

        const cutThumb = await createDisplayCutoutUrl(display, 480);
        if (cancelled || myToken !== runTokenRef.current) return;

        setCutoutPreview(cutThumb);
        setShowCompare(true);
        setProgress(38);
        setStatusLine(t.cutoutDone);

        await sleep(lite ? 400 : 900);
        if (cancelled || myToken !== runTokenRef.current) return;

        setCurrentStep(1);
        setProgress(42);
        setStatusLine(t.converting);

        let pet: Awaited<ReturnType<typeof generatePetVideo>> | null = null;

        if (LUMA_ENABLED) {
          try {
            pet = await generatePetVideo(cutFile, {
              userId: "anonymous",
              contentId: cut.content_id || undefined,
              skipPreprocessing: true,
            });
          } catch (e) {
            const msg =
              e instanceof Error ? e.message : typeof e === "string" ? e : "Luma failed";
            if (!isSkippableLumaError(msg)) throw e;
            await runFilmConversionDemo((pct, line) => {
              if (cancelled || myToken !== runTokenRef.current) return;
              setProgress(pct);
              setStatusLine(line);
            }, t);
          }
        } else {
          setStatusLine(t.lumaSkip);
          await runFilmConversionDemo((pct, line) => {
            if (cancelled || myToken !== runTokenRef.current) return;
            setProgress(pct);
            setStatusLine(line);
          }, t);
        }

        if (cancelled || myToken !== runTokenRef.current) return;

        const stored: StoredPipeline = {
          content_id: pet?.content_id || cutContentId || `fallback_${Date.now()}`,
          cutout_display_url: display,
          dog_only_nobg_url: pet?.dog_only_nobg_url || display,
          idle_video_url: pet?.idle_video_url || display,
          action_video_url: pet?.action_video_url || display,
        };
        try {
          sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(stored));
        } catch {
          /* ignore */
        }

        setProgress(100);
        setCurrentStep(2);
        setStatusLine(t.done);

        setTimeout(() => {
          if (cancelled || myToken !== runTokenRef.current) return;
          onCompleteRef.current(display);
        }, lite ? 400 : 700);
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : typeof e === "string" ? e : "Processing failed";
        fail(msg);
      } finally {
        if (!cancelled && myToken === runTokenRef.current) {
          setProcessingActive(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      setProcessingActive(false);
    };
  }, [uploadedImage, language]);

  const originalForUi = displayOriginal || uploadedImage;

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      <header className="px-8 pt-14 pb-4 text-center relative z-10 shrink-0">
        <h1
          className="text-lg font-light italic leading-relaxed px-2"
          style={{ color: "#F1E5D1" }}
        >
          {titles[titleIndex]}
        </h1>
      </header>

      <div className="flex-1 flex flex-col items-center px-6 relative z-10 min-h-0 overflow-y-auto hide-scrollbar">
        {showCompare && originalForUi && cutoutPreview ? (
          <CompareImages
            original={originalForUi}
            cutout={cutoutPreview}
            beforeLabel={t.before}
            afterLabel={t.after}
            showCheck={currentStep >= 1}
          />
        ) : originalForUi ? (
          <div className="w-40 h-40 mb-6 rounded-2xl overflow-hidden bg-[#141416] flex items-center justify-center">
            <img
              src={originalForUi}
              alt=""
              className="max-w-full max-h-full object-contain p-2"
              decoding="async"
            />
          </div>
        ) : null}

        {currentStep === 1 && !lite ? (
          <div className="processing-scanline w-40 h-1 mb-4 rounded-full overflow-hidden bg-white/5" />
        ) : null}

        <div className="w-full max-w-[300px] mb-6 shrink-0">
          <div className="flex items-center justify-between mb-4">
            {t.steps.map((step, index) => (
              <div key={step.id} className="flex flex-col items-center flex-1">
                <div
                  className="w-10 h-10 rounded-full flex items-center justify-center mb-1.5 text-xs font-medium"
                  style={{
                    background: currentStep >= index ? "rgba(28, 28, 30, 0.9)" : "rgba(20, 20, 22, 0.6)",
                    color: currentStep >= index ? "#c9a227" : "#666",
                  }}
                >
                  {currentStep > index ? <Check className="w-4 h-4" /> : step.id}
                </div>
                <span
                  className="text-[9px] tracking-wide text-center"
                  style={{ color: currentStep >= index ? "#F1E5D1" : "#555" }}
                >
                  {step.name}
                </span>
              </div>
            ))}
          </div>
          <div
            className={`h-[3px] rounded-full overflow-hidden bg-[rgba(28,28,30,0.8)] ${
              currentStep === 0 && progress < 40 ? "processing-bar-active" : ""
            }`}
          >
            <div
              className="h-full rounded-full transition-[width] duration-300 ease-out"
              style={{
                width: `${Math.max(progress, currentStep === 0 ? 12 : 0)}%`,
                background: "linear-gradient(90deg, #b8860b, #c9a227, #f5d77a)",
              }}
            />
          </div>
        </div>

        <div className="text-center mb-4 max-w-[300px]">
          <p className="text-sm font-light min-h-[1.25rem]" style={{ color: "#F1E5D1" }}>
            {currentStep === 0 ? rotatingStatus : t.steps[currentStep]?.description}
          </p>
          {statusLine ? (
            <p className="text-[11px] mt-2" style={{ color: "#888" }}>
              {statusLine}
            </p>
          ) : currentStep === 0 ? (
            <p className="text-[11px] mt-2 animate-pulse" style={{ color: "#888" }}>
              {t.waitHint}
            </p>
          ) : null}
          {!error ? (
            <p className="text-[10px] mt-1 tabular-nums" style={{ color: "#888" }}>
              {Math.floor(elapsedSec / 60)}:{String(elapsedSec % 60).padStart(2, "0")}
              {currentStep === 0 && elapsedSec >= 3 ? (
                <span className="block mt-1" style={{ color: "#666" }}>
                  {t.waitHint}
                </span>
              ) : null}
            </p>
          ) : null}
        </div>

        {error ? (
          <div
            className="mb-4 px-4 py-3 rounded-xl text-center text-sm max-w-[300px]"
            style={{ background: "rgba(80, 20, 20, 0.4)", color: "#f5c2c2", border: "1px solid #553333" }}
          >
            {error}
          </div>
        ) : null}

        <div className="flex items-center gap-2 mb-6 text-[#c9a227]">
          <Sparkles className="w-4 h-4" />
          <span className="text-[11px] text-[#888]">{m.brand}</span>
        </div>
      </div>
    </div>
  );
}
