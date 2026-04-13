"use client";

import { useState, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import { ArrowLeft, RotateCcw, Move, Maximize2, Film } from "lucide-react";
import {
  ETERNAL_BEAM_PIPELINE_KEY,
  type StoredPipeline,
} from "@/components/memorial/ai-processing-screen";
import { generatePreview, getVideoApiBaseUrl } from "@/app/services/videoProcessingApi";

interface PreviewScreenProps {
  cutoutImage: string | null;
  selectedTheme: number | null;
  settings: { scale: number; posX: number; posY: number };
  onSettingsChange: (settings: { scale: number; posX: number; posY: number }) => void;
  onComplete: () => void;
  onBack: () => void;
}

const themes = [
  { id: 1, name: "Celestial", gradient: "from-indigo-900 via-purple-900 to-black", accent: "#8b5cf6" },
  { id: 2, name: "Golden Meadow", gradient: "from-amber-900 via-yellow-900 to-black", accent: "#f59e0b" },
  { id: 3, name: "Starlight", gradient: "from-slate-900 via-zinc-800 to-black", accent: "#e4e4e7" },
  { id: 4, name: "Aurora", gradient: "from-emerald-900 via-teal-900 to-black", accent: "#10b981" },
  { id: 5, name: "Sunset", gradient: "from-rose-900 via-orange-900 to-black", accent: "#f43f5e" },
  { id: 6, name: "Ocean Deep", gradient: "from-blue-900 via-cyan-900 to-black", accent: "#06b6d4" },
];

/** Matches `backend/themes/{id}.mp4` when you add theme files (see THEMES_VIDEO_DIR). */
const THEME_PREVIEW_IDS: Record<number, string> = {
  1: "celestial",
  2: "golden_meadow",
  3: "starlight",
  4: "aurora",
  5: "sunset",
  6: "ocean_deep",
};

function isLikelyVideoUrl(url: string): boolean {
  const u = url.toLowerCase();
  return u.endsWith(".mp4") || u.endsWith(".webm") || u.endsWith(".mov");
}

export function PreviewScreen({
  cutoutImage,
  selectedTheme,
  settings,
  onSettingsChange,
  onComplete,
  onBack,
}: PreviewScreenProps) {
  const [activeSlider, setActiveSlider] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<StoredPipeline | null>(null);
  const [ffPreviewUrl, setFfPreviewUrl] = useState<string | null>(null);
  const [ffLoading, setFfLoading] = useState(false);
  const [ffError, setFfError] = useState<string | null>(null);
  const currentTheme = themes.find(t => t.id === selectedTheme) || themes[0];

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

  const tryFfmpegPreview = useCallback(async () => {
    if (!cutoutImage || !selectedTheme) {
      setFfError("Cutout or theme missing.");
      return;
    }
    const bgId = THEME_PREVIEW_IDS[selectedTheme];
    if (!bgId) {
      setFfError("Unknown theme for preview.");
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
      setFfError(e instanceof Error ? e.message : "Preview failed (add theme MP4 under backend/themes?)");
    } finally {
      setFfLoading(false);
    }
  }, [cutoutImage, selectedTheme, settings.posX, settings.posY, settings.scale]);

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
  }) => (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-3"
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
          className="text-xs font-light tabular-nums"
          style={{ color: "#c9a227" }}
        >
          {value.toFixed(label === "Scale" ? 1 : 0)}
        </span>
      </div>
      
      <div className="relative h-10 flex items-center">
        {/* Track Background */}
        <div 
          className="absolute inset-x-0 h-[3px] rounded-full"
          style={{ background: "#1C1C1E" }}
        />
        
        {/* Active Track */}
        <div 
          className="absolute h-[3px] rounded-full transition-all duration-150"
          style={{ 
            background: "linear-gradient(90deg, #c9a227, #f5d77a)",
            width: `${((value - min) / (max - min)) * 100}%`,
            boxShadow: activeSlider === id ? "0 0 10px rgba(201, 162, 39, 0.3)" : "none",
          }}
        />

        {/* Thumb */}
        <motion.div
          className="absolute w-5 h-5 rounded-full cursor-grab active:cursor-grabbing"
          style={{
            left: `calc(${((value - min) / (max - min)) * 100}% - 10px)`,
            background: "linear-gradient(135deg, #c9a227, #d4af37)",
            boxShadow: activeSlider === id 
              ? "0 0 20px rgba(201, 162, 39, 0.5), 0 2px 10px rgba(0,0,0,0.3)"
              : "0 2px 10px rgba(0,0,0,0.3)",
          }}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.95 }}
        />

        {/* Hidden Range Input */}
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          onFocus={() => setActiveSlider(id)}
          onBlur={() => setActiveSlider(null)}
          className="absolute inset-0 w-full h-full opacity-0 cursor-grab active:cursor-grabbing"
        />
      </div>
    </motion.div>
  );

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] min-h-0 overflow-hidden">
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
          Adjust
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

      {/* Preview Area */}
      <div className="px-6 py-4 flex-1 flex flex-col items-center justify-start min-h-0 overflow-y-auto gap-4">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          className="relative w-full aspect-[3/4] max-h-[320px] rounded-3xl overflow-hidden"
          style={{
            background: "#1C1C1E",
            border: "1px solid #333333",
            boxShadow: `0 0 60px ${currentTheme.accent}08`,
          }}
        >
          {/* Theme Background */}
          <div className={`absolute inset-0 bg-gradient-to-b ${currentTheme.gradient}`} />
          
          {/* Ambient Glow */}
          <motion.div
            className="absolute inset-0"
            style={{
              background: `radial-gradient(circle at center, ${currentTheme.accent}15 0%, transparent 60%)`,
            }}
            animate={{ opacity: [0.3, 0.5, 0.3] }}
            transition={{ duration: 3, repeat: Infinity }}
          />

          {/* Subject with transformations */}
          {cutoutImage && (
            <motion.div
              className="absolute inset-0 flex items-center justify-center"
              animate={{
                scale: settings.scale,
                x: settings.posX,
                y: settings.posY,
              }}
              transition={{ type: "spring", stiffness: 200, damping: 25 }}
            >
              <div 
                className="relative w-28 h-28 rounded-full overflow-hidden"
                style={{
                  boxShadow: `0 0 50px ${currentTheme.accent}50, 0 0 100px ${currentTheme.accent}25`,
                }}
              >
                <img src={cutoutImage} alt="Subject" className="w-full h-full object-cover" />
              </div>
            </motion.div>
          )}

          {/* Hologram Scanlines */}
          <div className="absolute inset-0 opacity-10 pointer-events-none overflow-hidden">
            {[...Array(12)].map((_, i) => (
              <motion.div
                key={i}
                className="absolute left-0 right-0 h-[1px]"
                style={{
                  top: `${8 + i * 8}%`,
                  background: `linear-gradient(90deg, transparent, ${currentTheme.accent}, transparent)`,
                }}
                animate={{ opacity: [0.2, 0.6, 0.2] }}
                transition={{ duration: 2, delay: i * 0.15, repeat: Infinity }}
              />
            ))}
          </div>

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

        {/* Pipeline: Luma clips + Unity placeholder + optional FFmpeg composite */}
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-5 w-full max-w-[340px] space-y-3"
        >
          <div className="flex items-center gap-2 text-[11px] tracking-wider" style={{ color: "#888" }}>
            <Film className="w-3.5 h-3.5" strokeWidth={1.5} />
            <span>Pipeline (Luma → Unity background)</span>
          </div>
          <p className="text-[10px] leading-relaxed" style={{ color: "#666" }}>
            Unity will supply the final background composite. Below: Luma outputs from the server; FFmpeg row is optional if{" "}
            <code className="text-[9px]">backend/themes/{`{theme}`}.mp4</code> exists.
          </p>
          {pipeline?.idle_video_url || pipeline?.action_video_url ? (
            <div className="grid grid-cols-2 gap-2">
              {pipeline.idle_video_url ? (
                <div className="space-y-1">
                  <span className="text-[9px] uppercase tracking-wider" style={{ color: "#888" }}>
                    Idle
                  </span>
                  {isLikelyVideoUrl(pipeline.idle_video_url) ? (
                    <video
                      src={pipeline.idle_video_url}
                      className="w-full rounded-lg border border-white/10 max-h-[88px] object-cover bg-black"
                      controls
                      muted
                      playsInline
                      loop
                    />
                  ) : (
                    <img
                      src={pipeline.idle_video_url}
                      alt="Idle fallback"
                      className="w-full rounded-lg border border-white/10 max-h-[88px] object-cover bg-black"
                    />
                  )}
                </div>
              ) : null}
              {pipeline.action_video_url ? (
                <div className="space-y-1">
                  <span className="text-[9px] uppercase tracking-wider" style={{ color: "#888" }}>
                    Action
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
              No Luma URLs in session — complete AI processing with the API running.
            </p>
          )}
          <div
            className="rounded-xl p-3 border border-dashed"
            style={{ borderColor: `${currentTheme.accent}40`, background: "rgba(0,0,0,0.35)" }}
          >
            <p className="text-[11px] font-light mb-2" style={{ color: "#A1A1A6" }}>
              Unity layer (placeholder): background plate + subject video / cutout on device.
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
              {ffLoading ? "Generating server preview…" : "Try FFmpeg composite preview"}
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
          label="Scale"
          icon={Maximize2}
          value={settings.scale}
          min={0.5}
          max={2}
          step={0.1}
          onChange={(val) => onSettingsChange({ ...settings, scale: val })}
        />

        <SliderControl
          id="posX"
          label="Position X"
          icon={Move}
          value={settings.posX}
          min={-100}
          max={100}
          step={1}
          onChange={(val) => onSettingsChange({ ...settings, posX: val })}
        />

        <SliderControl
          id="posY"
          label="Position Y"
          icon={Move}
          value={settings.posY}
          min={-100}
          max={100}
          step={1}
          onChange={(val) => onSettingsChange({ ...settings, posY: val })}
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
          Complete
        </motion.button>
      </div>
    </div>
  );
}
