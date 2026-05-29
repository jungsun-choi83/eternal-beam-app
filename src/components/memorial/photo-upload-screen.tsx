"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, Check, Video, Play, Pause } from "lucide-react";
import { HologramEffects } from "./hologram-effects";
import { createDisplayImageUrl } from "@/lib/display-image";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { MediaFileTrigger } from "@/components/memorial/media-file-trigger";
import { PhotoUploadGuide } from "@/components/memorial/photo-upload-guide";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import { MemorialIconButton, MemorialPrimaryButton } from "@/components/memorial/memorial-chrome";
import { inferMediaKind } from "@/lib/media-file-kind";
import { CUTOUT_WARMUP_MAX_MS } from "@/lib/cutout-speed-mode";
import { warmupVideoApi } from "@/lib/video-api-warmup";

interface PhotoUploadScreenProps {
  uploadedImage: string | null;
  language?: string;
  onImageUpload: (imageUrl: string) => void;
  onContinue: () => void;
  onBack: () => void;
}

export function PhotoUploadScreen({
  uploadedImage,
  language = "ko",
  onImageUpload,
  onContinue,
  onBack,
}: PhotoUploadScreenProps) {
  const m = memorialT(language);
  const u = m.upload;
  const c = m.common;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [mediaType, setMediaType] = useState<"image" | "video" | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const ingestFile = useCallback(
    (file: File) => {
      const kind = inferMediaKind(file);
      if (!kind) return;

      if (kind === "image") {
        setMediaType("image");
        localStorage.setItem("eternal_beam_media_type", "image");
        void warmupVideoApi({ coldStart: true, maxWaitMs: CUTOUT_WARMUP_MAX_MS });
        const reader = new FileReader();
        reader.onload = () => onImageUpload(reader.result as string);
        reader.readAsDataURL(file);
        return;
      }

      setMediaType("video");
      localStorage.setItem("eternal_beam_media_type", "video");
      if (file.size > 100 * 1024 * 1024) return;
      onImageUpload(URL.createObjectURL(file));
    },
    [onImageUpload],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const file = e.dataTransfer.files[0];
      if (file) ingestFile(file);
    },
    [ingestFile],
  );

  useEffect(() => {
    if (!uploadedImage) return;
    const stored = localStorage.getItem("eternal_beam_media_type");
    if (stored === "image" || stored === "video") setMediaType(stored);
    else if (uploadedImage.startsWith("blob:")) setMediaType("video");
    else if (uploadedImage.startsWith("data:image/")) setMediaType("image");
  }, [uploadedImage]);

  useEffect(() => {
    if (!uploadedImage?.startsWith("data:image/")) {
      setPreviewUrl(uploadedImage);
      return;
    }
    let cancelled = false;
    createDisplayImageUrl(uploadedImage, 480).then((url) => {
      if (!cancelled) setPreviewUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [uploadedImage]);

  const imageForDisplay = previewUrl || uploadedImage;

  const togglePlayPause = () => {
    if (videoRef.current) {
      if (isPlaying) {
        videoRef.current.pause();
      } else {
        videoRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  const formatPills = ["HEIC", "JPG", "PNG", "MP4"];

  return (
    <div className="memorial-screen h-full flex flex-col relative overflow-hidden">
      <HologramEffects />

      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative z-10">
        <MemorialIconButton
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          aria-label={c.back}
        >
          <span className="text-lg leading-none" aria-hidden>
            ←
          </span>
        </MemorialIconButton>

        <motion.h1
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="screen-title absolute left-1/2 -translate-x-1/2"
          style={{ color: "#F5F5F7" }}
        >
          {u.title}
        </motion.h1>

        <div className="w-10" />
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-6 py-2 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="w-full max-w-[320px] mx-auto relative z-10"
        >
          <p className="memorial-eyebrow text-center mb-2">01 · Photo</p>
          <h2 className="upload-title text-center">{u.heading}</h2>
          <p className="upload-subtitle text-center mt-1">{u.subtitle}</p>

          <PhotoUploadGuide language={language} />

          <MediaFileTrigger
            onFile={ingestFile}
            className="block mt-4 touch-manipulation"
          >
            <motion.div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`upload-card relative overflow-hidden aspect-[4/5] ${
                isDragging ? "drag-over" : ""
              }`}
            >
              {uploadedImage ? (
                <>
                  {mediaType === "video" ? (
                    <div className="relative w-full h-full cutout-stage cutout-stage--fill">
                      <video
                        ref={videoRef}
                        src={uploadedImage}
                        className="cutout-stage__subject"
                        loop
                        muted
                        playsInline
                      />
                      <button
                        type="button"
                        onClick={(e) => {
                          e.preventDefault();
                          togglePlayPause();
                        }}
                        className="absolute inset-0 flex items-center justify-center bg-black/20"
                      >
                        <div className="w-14 h-14 rounded-full flex items-center justify-center bg-white/15 border border-white/20">
                          {isPlaying ? (
                            <Pause className="w-6 h-6 text-white" fill="white" />
                          ) : (
                            <Play className="w-6 h-6 text-white ml-1" fill="white" />
                          )}
                        </div>
                      </button>
                    </div>
                  ) : (
                    <CutoutStage className="w-full h-full" fit="cover">
                      <img
                        src={imageForDisplay || uploadedImage}
                        alt=""
                        className="cutout-stage__subject"
                        decoding="async"
                      />
                    </CutoutStage>
                  )}
                  <div className="absolute top-3 right-3 w-8 h-8 rounded-full flex items-center justify-center z-10"
                    style={{
                      background: "linear-gradient(135deg, #c9a227, #e8d5a3)",
                      boxShadow: "0 4px 12px rgba(0,0,0,0.35)",
                    }}
                  >
                    <Check className="w-4 h-4 text-[#0a0a0a]" strokeWidth={3} />
                  </div>
                  <div className="absolute bottom-3 left-3 px-2 py-1 rounded-full bg-black/50 text-[10px] text-white">
                    {mediaType === "video" ? c.video : c.photo}
                  </div>
                </>
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 px-6 py-8">
                  <div className="upload-card__empty-icon">
                    <Upload className="w-6 h-6 text-[#e8d5a3]" strokeWidth={1.25} />
                  </div>
                  <div className="text-center">
                    <p className="text-[15px] font-light text-[#f1e5d1] leading-snug">
                      {isDragging ? u.drop : u.drag}
                    </p>
                    <p className="memorial-caption mt-2 opacity-80">{u.tapBrowse}</p>
                  </div>
                  <div className="upload-formats">
                    {formatPills.map((f) => (
                      <span key={f} className="upload-format-pill">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </motion.div>
          </MediaFileTrigger>

          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.4 }}
            className="text-center text-[11px] mt-6 font-light leading-relaxed memorial-body"
          >
            {u.hint}
          </motion.p>
        </motion.div>
      </div>

      <div className="px-8 pb-10 relative z-10">
        <MemorialPrimaryButton
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onContinue}
          disabled={!uploadedImage}
        >
          {u.continue}
        </MemorialPrimaryButton>
      </div>
    </div>
  );
}
