"use client";

import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles } from "lucide-react";
import {
  cutoutImage,
  generatePetVideo,
  type CutoutResult,
} from "@/app/services/videoProcessingApi";

export const ETERNAL_BEAM_PIPELINE_KEY = "eternal_beam_pipeline_v1";

export interface StoredPipeline {
  content_id: string;
  cutout_display_url: string;
  dog_only_nobg_url: string;
  idle_video_url: string;
  action_video_url: string;
}

interface AIProcessingScreenProps {
  uploadedImage: string | null;
  onComplete: (cutoutUrl: string) => void;
}

function dataUrlToFile(dataUrl: string, filename: string): File {
  const arr = dataUrl.split(",");
  const mime = arr[0].match(/:(.*?);/)?.[1] || "image/jpeg";
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8arr = new Uint8Array(n);
  while (n--) u8arr[n] = bstr.charCodeAt(n);
  return new File([u8arr], filename, { type: mime });
}

function cutoutDisplayUrl(result: CutoutResult): string {
  if (result.cutout_url) return result.cutout_url;
  if (result.cutout_png_base64)
    return `data:image/png;base64,${result.cutout_png_base64}`;
  return "";
}

function isInsufficientCreditsError(message: string): boolean {
  const m = message.toLowerCase();
  return m.includes("insufficient credit") || m.includes("크레딧");
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
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") {
        throw new Error("누끼 URL 다운로드 시간 초과(2분). Supabase 공개 URL·네트워크를 확인하세요.");
      }
      throw e;
    } finally {
      clearTimeout(tid);
    }
    if (!r.ok) {
      throw new Error(`누끼 이미지를 불러오지 못했습니다 (${r.status}).`);
    }
    const blob = await r.blob();
    // Storage/CDN often serves PNG as application/octet-stream; FastAPI requires image/*
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

const processingSteps = [
  { id: 1, name: "Cutout", description: "Removing background (rembg)" },
  { id: 2, name: "Luma", description: "Generating idle & action clips (may take minutes)" },
  { id: 3, name: "Ready", description: "Pipeline complete" },
];

const emotionalQuotes = [
  "Those we love never truly leave us...",
  "Forever in our hearts, now in light...",
  "Memories become eternal moments...",
  "Love transcends all boundaries...",
  "Creating a bridge between worlds...",
];

export function AIProcessingScreen({ uploadedImage, onComplete }: AIProcessingScreenProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [progress, setProgress] = useState(5);
  const [elapsedSec, setElapsedSec] = useState(0);
  const [quoteIndex, setQuoteIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [statusLine, setStatusLine] = useState("");
  const runTokenRef = useRef(0);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useEffect(() => {
    const quoteInterval = setInterval(() => {
      setQuoteIndex((prev) => (prev + 1) % emotionalQuotes.length);
    }, 4000);
    return () => clearInterval(quoteInterval);
  }, []);

  useEffect(() => {
    if (!uploadedImage) return;
    setElapsedSec(0);
    const tick = setInterval(() => setElapsedSec((s) => s + 1), 1000);
    return () => clearInterval(tick);
  }, [uploadedImage]);

  useEffect(() => {
    if (!uploadedImage) return;

    const myToken = ++runTokenRef.current;
    let cancelled = false;

    const fail = (msg: string) => {
      if (cancelled || myToken !== runTokenRef.current) return;
      setError(msg);
      setProgress(0);
    };

    (async () => {
      setError(null);
      setCurrentStep(0);
      setProgress(10);
      setStatusLine("Uploading for cutout…");

      try {
        const file = dataUrlToFile(uploadedImage, "upload.jpg");
        const cut = await cutoutImage(file, {
          userId: "anonymous",
          saveToStorage: true,
          model: "isnet-general-use",
        });

        if (cancelled || myToken !== runTokenRef.current) return;

        let display = cutoutDisplayUrl(cut);
        let cutFile: File;
        if (!display) {
          if (cut.error && isCutoutMemoryError(cut.error)) {
            // rembg OOM이면 원본으로 계속 진행해 Luma/후속 플로우 테스트를 막지 않는다.
            setStatusLine("Cutout memory limit hit. Continuing with original image.");
            display = uploadedImage;
            cutFile = file;
          } else {
            throw new Error(cut.error || "Cutout failed");
          }
        } else {
          cutFile = await cutoutResultToFile(cut);
        }

        setProgress(35);
        setCurrentStep(1);
        setStatusLine("Luma: generating videos (this can take several minutes)…");
        let pet: Awaited<ReturnType<typeof generatePetVideo>> | null = null;
        try {
          pet = await generatePetVideo(cutFile, {
            userId: "anonymous",
            contentId: cut.content_id || undefined,
            skipPreprocessing: true,
          });
        } catch (e) {
          const msg =
            e instanceof Error ? e.message : typeof e === "string" ? e : "Luma generation failed";
          if (!isInsufficientCreditsError(msg)) throw e;
          setStatusLine("Luma credits unavailable. Using cutout fallback so you can continue testing.");
        }

        if (cancelled || myToken !== runTokenRef.current) return;

        const stored: StoredPipeline = {
          content_id: pet?.content_id || cut.content_id || `fallback_${Date.now()}`,
          cutout_display_url: display,
          dog_only_nobg_url: pet?.dog_only_nobg_url || display,
          idle_video_url: pet?.idle_video_url || display,
          action_video_url: pet?.action_video_url || display,
        };
        try {
          sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(stored));
        } catch {
          /* ignore quota */
        }

        setProgress(100);
        setCurrentStep(2);
        setStatusLine("Done.");

        setTimeout(() => {
          if (cancelled || myToken !== runTokenRef.current) return;
          onCompleteRef.current(display);
        }, 600);
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : typeof e === "string" ? e : "Processing failed";
        fail(msg);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [uploadedImage]);

  useEffect(() => {
    const t = setInterval(() => {
      setProgress((p) => {
        if (p >= 95 || error) return p;
        return Math.min(p + 0.35, 94);
      });
    }, 400);
    return () => clearInterval(t);
  }, [error]);

  const previewSrc = uploadedImage;

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      <div className="absolute inset-0 pointer-events-none">
        <motion.div
          className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[400px] h-[400px]"
          style={{
            background: "radial-gradient(circle, rgba(201, 162, 39, 0.08) 0%, transparent 60%)",
            filter: "blur(60px)",
          }}
          animate={{
            scale: [1, 1.3, 1],
            opacity: [0.3, 0.5, 0.3],
          }}
          transition={{
            duration: 4,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
      </div>

      <header className="px-8 pt-16 pb-6 text-center relative z-10">
        <motion.h1
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          className="text-xl font-light relative inline-block"
          style={{ color: "#F1E5D1" }}
        >
          <span
            className="absolute inset-0 blur-[10px] opacity-40"
            style={{ color: "#F1E5D1" }}
          >
            Processing
          </span>
          <span className="relative">Processing</span>
        </motion.h1>
      </header>

      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.8 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.3 }}
          className="relative mb-14"
        >
          <motion.div
            className="absolute -inset-8 rounded-full"
            style={{ border: "1px solid rgba(201, 162, 39, 0.15)" }}
            animate={{ rotate: 360 }}
            transition={{ duration: 12, repeat: Infinity, ease: "linear" }}
          />
          <motion.div
            className="absolute -inset-12 rounded-full"
            style={{ border: "1px solid rgba(201, 162, 39, 0.08)" }}
            animate={{ rotate: -360 }}
            transition={{ duration: 18, repeat: Infinity, ease: "linear" }}
          />

          <motion.div
            className="relative w-36 h-36 rounded-full overflow-hidden flex items-center justify-center"
            style={{
              background: "rgba(10, 10, 10, 0.6)",
            }}
            animate={{
              boxShadow: [
                "0 0 30px rgba(201, 162, 39, 0.15), 0 0 60px rgba(201, 162, 39, 0.05)",
                "0 0 60px rgba(201, 162, 39, 0.35), 0 0 120px rgba(201, 162, 39, 0.15)",
                "0 0 30px rgba(201, 162, 39, 0.15), 0 0 60px rgba(201, 162, 39, 0.05)",
              ],
            }}
            transition={{
              duration: 1.5,
              repeat: Infinity,
              ease: "easeInOut",
            }}
          >
            <motion.div
              className="absolute inset-0 rounded-full"
              style={{
                border: "2px solid rgba(201, 162, 39, 0.5)",
              }}
              animate={{
                opacity: [0.3, 0.8, 0.3],
                scale: [1, 1.02, 1],
              }}
              transition={{
                duration: 1.5,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />

            <motion.div
              className="absolute inset-2 rounded-full"
              style={{
                background: "radial-gradient(circle, rgba(201, 162, 39, 0.15) 0%, transparent 70%)",
              }}
              animate={{
                opacity: [0.2, 0.6, 0.2],
                scale: [0.9, 1.1, 0.9],
              }}
              transition={{
                duration: 1.5,
                repeat: Infinity,
                ease: "easeInOut",
              }}
            />

            {previewSrc ? (
              <motion.img
                src={previewSrc}
                alt="Processing"
                className="w-full h-full object-cover absolute inset-0"
                animate={{
                  filter: ["brightness(0.9)", "brightness(1.2)", "brightness(0.9)"],
                  opacity: [0.8, 1, 0.8],
                }}
                transition={{
                  duration: 1.5,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
              />
            ) : null}

            <motion.div
              className="absolute inset-0"
              style={{
                background: "linear-gradient(180deg, transparent 0%, rgba(201, 162, 39, 0.2) 100%)",
              }}
              animate={{ opacity: [0.3, 0.7, 0.3] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
          </motion.div>

          <motion.div
            className="absolute -bottom-2 -right-2 w-9 h-9 rounded-full flex items-center justify-center"
            style={{
              background: "linear-gradient(135deg, #c9a227 0%, #d4af37 100%)",
              boxShadow: "0 4px 20px rgba(201, 162, 39, 0.45)",
            }}
            animate={{ scale: [1, 1.1, 1] }}
            transition={{ duration: 1.5, repeat: Infinity }}
          >
            <Sparkles className="w-4 h-4 text-[#0a0a0a]" />
          </motion.div>
        </motion.div>

        <div className="w-full max-w-[280px] mb-10">
          <div className="flex items-center justify-between mb-5">
            {processingSteps.map((step, index) => (
              <motion.div
                key={step.id}
                className="flex flex-col items-center"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.4 + index * 0.1 }}
              >
                <motion.div
                  className="w-11 h-11 rounded-full flex items-center justify-center mb-2 transition-all duration-500 relative"
                  style={{
                    background: currentStep >= index ? "rgba(28, 28, 30, 0.8)" : "rgba(20, 20, 22, 0.6)",
                    boxShadow:
                      currentStep === index
                        ? "0 0 20px rgba(201, 162, 39, 0.25)"
                        : "none",
                  }}
                  animate={currentStep === index ? { scale: [1, 1.05, 1] } : {}}
                  transition={{ duration: 1, repeat: Infinity }}
                >
                  <div
                    className="absolute top-0 left-0 right-0 h-px rounded-t-full"
                    style={{
                      background:
                        currentStep >= index
                          ? "linear-gradient(90deg, rgba(201, 162, 39, 0.3), rgba(201, 162, 39, 0.15), transparent)"
                          : "linear-gradient(90deg, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0.05), transparent)",
                    }}
                  />
                  <div
                    className="absolute top-0 left-0 bottom-0 w-px rounded-l-full"
                    style={{
                      background:
                        currentStep >= index
                          ? "linear-gradient(180deg, rgba(201, 162, 39, 0.3), rgba(201, 162, 39, 0.15), transparent)"
                          : "linear-gradient(180deg, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0.05), transparent)",
                    }}
                  />
                  <span
                    className="text-xs font-medium transition-colors duration-300"
                    style={{ color: currentStep >= index ? "#c9a227" : "#E2E2E2" }}
                  >
                    {step.id}
                  </span>
                </motion.div>
                <span
                  className="text-[10px] tracking-wider transition-colors duration-300"
                  style={{ color: currentStep >= index ? "#F1E5D1" : "#666666" }}
                >
                  {step.name}
                </span>
              </motion.div>
            ))}
          </div>

          <div
            className="h-[3px] rounded-full overflow-hidden relative"
            style={{ background: "rgba(28, 28, 30, 0.8)" }}
          >
            <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-white/10 via-white/05 to-transparent" />
            <motion.div
              className="h-full rounded-full relative"
              style={{
                width: `${progress}%`,
                background: "linear-gradient(90deg, #b8860b 0%, #c9a227 50%, #f5d77a 100%)",
              }}
            />
          </div>
        </div>

        <AnimatePresence mode="wait">
          <motion.div
            key={currentStep}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            className="text-center mb-4 max-w-[300px]"
          >
            <p className="text-sm font-light relative inline-block" style={{ color: "#F1E5D1" }}>
              <span className="absolute inset-0 blur-[6px] opacity-30">
                {processingSteps[currentStep]?.description}
              </span>
              <span className="relative">{processingSteps[currentStep]?.description}</span>
            </p>
            {statusLine ? (
              <p className="text-[11px] mt-2 font-extralight" style={{ color: "#888" }}>
                {statusLine}
              </p>
            ) : null}
            {!error ? (
              <p className="text-[10px] mt-2 leading-snug px-1" style={{ color: "#666" }}>
                경과 {Math.floor(elapsedSec / 60)}분 {elapsedSec % 60}초
                {currentStep >= 1
                  ? " · Luma 영상 생성은 보통 5~20분 걸립니다. 이 화면을 유지해 주세요."
                  : ""}
              </p>
            ) : null}
          </motion.div>
        </AnimatePresence>

        {error ? (
          <div
            className="mb-6 px-4 py-3 rounded-xl text-center text-sm max-w-[300px]"
            style={{ background: "rgba(80, 20, 20, 0.4)", color: "#f5c2c2", border: "1px solid #553333" }}
          >
            {error}
          </div>
        ) : null}

        <AnimatePresence mode="wait">
          <motion.p
            key={quoteIndex}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            transition={{ duration: 0.8 }}
            className="text-[13px] font-extralight italic tracking-wide text-center max-w-[260px]"
            style={{ color: "#E2E2E2" }}
          >
            &ldquo;{emotionalQuotes[quoteIndex]}&rdquo;
          </motion.p>
        </AnimatePresence>
      </div>

      <div className="h-20 relative">
        <div
          className="absolute bottom-0 left-0 right-0 h-32"
          style={{
            background: "linear-gradient(to top, rgba(201, 162, 39, 0.02) 0%, transparent 100%)",
          }}
        />
      </div>
    </div>
  );
}
