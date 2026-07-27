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
import { getMemorialTheme } from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
import { PetIdleDisplay } from "@/components/memorial/pet-idle-display";
import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";
import { getEffectiveBgVideo } from "@/lib/custom-background-store";
import {
  getThemeBackgroundApiId,
  resolveSelectedThemeId,
} from "@/lib/theme-selection-store";
import { isLikelyVideoUrl } from "@/lib/video-url";

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
  const previewThemeId = resolveSelectedThemeId(selectedTheme);
  const currentTheme =
    (previewThemeId != null ? getMemorialTheme(previewThemeId) : undefined) ??
    getMemorialTheme(1)!;
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

  const applySubjectTransform = useCallback((s: { scale: number; posX: number; posY: number }) => {
    const el = subjectLayerRef.current;
    if (!el) return;
    el.style.transform = `translate3d(${s.posX}px, ${s.posY}px, 0) scale(${s.scale})`;
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
  const idleVideoUrl = resolveIdleVideoUrl(pipeline?.idle_video_url, cutoutDisplay);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (raw) setPipeline(JSON.parse(raw) as StoredPipeline);
    } catch {
      setPipeline(null);
    }
  }, [cutoutImage]);

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
      g.pointers.delete(e.pointerId);
      g.startPoints.delete(e.pointerId);
      if (g.pointers.size === 0) {
        g.pinchStartDistance = null;
        finishSliderDrag();
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

          {/* Subject with transformations — first composite with selected theme bg */}
          {cutoutDisplay && (
            <div
              ref={subjectLayerRef}
              className="absolute inset-0 flex items-center justify-center p-4 preview-subject-layer"
              style={{
                transform: `translate3d(${displaySettings.posX}px, ${displaySettings.posY}px, 0) scale(${displaySettings.scale})`,
              }}
            >
              <PetIdleDisplay
                idleVideoUrl={pipeline?.idle_video_url}
                cutoutUrl={cutoutDisplay}
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
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
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
          {deliveryMode === "shipping" ? p.completeShipping : p.completeDevice}
        </motion.button>
      </div>
    </div>
  );
}
