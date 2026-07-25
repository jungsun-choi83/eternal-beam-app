"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, RotateCcw, Move, Maximize2, Film, Minus, Plus } from "lucide-react";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { generatePreview, getVideoApiBaseUrl, resolveIdleVideoUrl } from "@/app/services/videoProcessingApi";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { getMemorialTheme } from "@/components/memorial/themes";
import { ThemeBackgroundVideo } from "@/components/memorial/theme-background-video";
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

export function PreviewScreen({
  cutoutImage,
  selectedTheme,
  language = "ko",
  settings,
  onSettingsChange,
  onComplete,
  onBack,
}: PreviewScreenProps) {
  const p = memorialT(language).preview;
  const [activeSlider, setActiveSlider] = useState<string | null>(null);
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
  settingsRef.current = settings;

  useEffect(() => {
    if (previewThemeId != null) {
      assertPreviewTheme(selectedTheme, previewThemeId);
    }
  }, [selectedTheme, previewThemeId]);

  const idleVideoUrl = resolveIdleVideoUrl(pipeline?.idle_video_url);

  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY);
      if (raw) setPipeline(JSON.parse(raw) as StoredPipeline);
    } catch {
      setPipeline(null);
    }
  }, [cutoutImage]);

  const handleReset = useCallback(() => {
    onSettingsChange({ scale: 1, posX: 0, posY: 0 });
  }, [onSettingsChange]);

  const clampScale = (value: number) =>
    Math.round(Math.min(2, Math.max(0.5, value)) * 100) / 100;

  const clampPos = (value: number) =>
    Math.round(Math.min(100, Math.max(-100, value)) * 10) / 10;

  const handlePreviewWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const delta = -e.deltaY * 0.0025;
      const next = clampScale(settingsRef.current.scale + delta);
      onSettingsChange({
        ...settingsRef.current,
        scale: next,
      });
    },
    [onSettingsChange]
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
        scale: settings.scale,
        position_x: settings.posX,
        position_y: settings.posY,
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
  }, [cutoutImage, previewThemeId, currentTheme, settings.posX, settings.posY, settings.scale, p.cutoutMissing, p.themeUnknown, p.previewFailed]);

  const SliderControl = ({ 
    label, 
    icon: Icon, 
    value, 
    min, 
    max, 
    step,
    onChange,
    id,
  }: { 
    label: string;
    icon: React.ElementType;
    value: number;
    min: number;
    max: number;
    step: number;
    onChange: (value: number) => void;
    id: string;
  }) => {
    const clamp = (v: number) => Math.min(max, Math.max(min, v));
    const formatValue = (v: number) =>
      id === "scale" ? v.toFixed(2) : v.toFixed(1);
    const normalize = (v: number) =>
      id === "scale" ? clampScale(v) : clampPos(v);
    const bump = (dir: -1 | 1) => {
      const next = clamp(value + dir * step);
      onChange(normalize(next));
    };
    const handleSliderInput = (raw: string) => {
      const parsed = Number.parseFloat(raw);
      if (Number.isNaN(parsed)) return;
      onChange(normalize(parsed));
    };

    const stepButtonStyle = {
      background: "#1C1C1E",
      border: "1px solid #333333",
      color: "#F5F5F7",
    } as const;

    return (
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="space-y-2"
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Icon className="w-4 h-4" style={{ color: "#A1A1A6" }} strokeWidth={1.5} />
            <span
              className="text-xs font-light tracking-wider"
              style={{ color: "#A1A1A6" }}
            >
              {label}
            </span>
          </div>
          <span
            className="text-xs font-light tabular-nums min-w-[2.5rem] text-right"
            style={{ color: "#c9a227" }}
          >
            {formatValue(value)}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <motion.button
            type="button"
            aria-label={`${label} decrease`}
            onClick={() => bump(-1)}
            className="w-9 h-9 rounded-full flex items-center justify-center shrink-0"
            style={stepButtonStyle}
            whileTap={{ scale: 0.92 }}
          >
            <Minus className="w-4 h-4" strokeWidth={1.5} />
          </motion.button>

          <div className="relative h-10 flex-1 flex items-center min-w-0">
            <div
              className="absolute inset-x-0 h-[3px] rounded-full"
              style={{ background: "#1C1C1E" }}
            />
            <div
              className="absolute h-[3px] rounded-full"
              style={{
                background: "linear-gradient(90deg, #c9a227, #f5d77a)",
                width: `${((value - min) / (max - min)) * 100}%`,
                transition: activeSlider === id ? "none" : "width 120ms ease-out",
                boxShadow:
                  activeSlider === id
                    ? "0 0 10px rgba(201, 162, 39, 0.3)"
                    : "none",
              }}
            />
            <motion.div
              className="absolute w-5 h-5 rounded-full pointer-events-none"
              style={{
                left: `calc(${((value - min) / (max - min)) * 100}% - 10px)`,
                background: "linear-gradient(135deg, #c9a227, #d4af37)",
                transition: activeSlider === id ? "none" : "left 120ms ease-out",
                boxShadow:
                  activeSlider === id
                    ? "0 0 20px rgba(201, 162, 39, 0.5), 0 2px 10px rgba(0,0,0,0.3)"
                    : "0 2px 10px rgba(0,0,0,0.3)",
              }}
            />
            <input
              type="range"
              min={min}
              max={max}
              step={step}
              value={value}
              onInput={(e) => handleSliderInput(e.currentTarget.value)}
              onChange={(e) => handleSliderInput(e.target.value)}
              onPointerDown={() => setActiveSlider(id)}
              onFocus={() => setActiveSlider(id)}
              onBlur={() => setActiveSlider(null)}
              className="preview-adjust-slider absolute inset-0 w-full h-full opacity-0 cursor-grab active:cursor-grabbing touch-pan-x"
              aria-label={label}
            />
          </div>

          <motion.button
            type="button"
            aria-label={`${label} increase`}
            onClick={() => bump(1)}
            className="w-9 h-9 rounded-full flex items-center justify-center shrink-0"
            style={stepButtonStyle}
            whileTap={{ scale: 0.92 }}
          >
            <Plus className="w-4 h-4" strokeWidth={1.5} />
          </motion.button>
        </div>
      </motion.div>
    );
  };

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

      {/* Preview Area */}
      <div className="px-6 py-4 flex-1 flex flex-col items-center justify-start min-h-0 overflow-hidden gap-4">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="theme-preview-frame relative w-full aspect-[3/4] max-h-[320px]"
          onWheel={handlePreviewWheel}
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
          {cutoutImage && (
            <div
              className="absolute inset-0 flex items-center justify-center p-4"
              style={{
                transform: `translate(${settings.posX}px, ${settings.posY}px) scale(${settings.scale})`,
              }}
            >
              <IdleLoopVideo
                src={idleVideoUrl}
                className="theme-preview-frame__pet max-h-[62%] max-w-[92%]"
                style={{
                  filter: `drop-shadow(0 16px 32px ${currentTheme.accent}66)`,
                }}
              />
            </div>
          )}

          {/* Corner Guides */}
          {["top-3 left-3", "top-3 right-3", "bottom-3 left-3", "bottom-3 right-3"].map((pos, i) => (
            <div key={i} className={`absolute ${pos} w-4 h-4`}>
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

      {/* Controls + Complete - 하단 고정 */}
      <div 
        className="px-8 py-6 space-y-6 shrink-0"
        style={{
          background: "linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%)",
        }}
      >
        <SliderControl
          id="scale"
          label={p.scale}
          icon={Maximize2}
          value={settings.scale}
          min={0.5}
          max={2}
          step={0.01}
          onChange={(val) => onSettingsChange({ ...settingsRef.current, scale: val })}
        />
        <p className="text-[10px] text-center -mt-3" style={{ color: "#6b6b70" }}>
          {p.scaleWheelHint}
        </p>

        <SliderControl
          id="posX"
          label={p.posX}
          icon={Move}
          value={settings.posX}
          min={-100}
          max={100}
          step={0.5}
          onChange={(val) => onSettingsChange({ ...settingsRef.current, posX: val })}
        />

        <SliderControl
          id="posY"
          label={p.posY}
          icon={Move}
          value={settings.posY}
          min={-100}
          max={100}
          step={0.5}
          onChange={(val) => onSettingsChange({ ...settingsRef.current, posY: val })}
        />
      </div>

      {/* Complete Button - 항상 보임 */}
      <div className="px-8 pb-10 shrink-0">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
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
          {p.completeSend}
        </motion.button>
      </div>
    </div>
  );
}
