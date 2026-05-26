"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, ArrowLeft, Check, Image as ImageIcon, Video, Play, Pause } from "lucide-react";
import { HologramEffects } from "./hologram-effects";
import { isLiteUI } from "@/lib/ui-performance";
import { createDisplayImageUrl } from "@/lib/display-image";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { MediaFileTrigger } from "@/components/memorial/media-file-trigger";
import { inferMediaKind } from "@/lib/media-file-kind";

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
  const lite = isLiteUI();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [isGlowing, setIsGlowing] = useState(false);
  const [mediaType, setMediaType] = useState<"image" | "video" | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
    setIsGlowing(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    setIsGlowing(false);
  }, []);

  const ingestFile = useCallback(
    (file: File) => {
      const kind = inferMediaKind(file);
      if (!kind) return;

      if (kind === "image") {
        setMediaType("image");
        localStorage.setItem("eternal_beam_media_type", "image");
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
      setIsGlowing(false);

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

  return (
    <div className="h-full flex flex-col bg-[#0a0a0a] relative overflow-hidden">
      <HologramEffects />
      {!lite ? (
        <div
          className="absolute inset-0 pointer-events-none opacity-60"
          style={{
            background:
              "radial-gradient(circle at 70% 20%, rgba(244,114,106,0.12) 0%, transparent 50%), radial-gradient(circle at 20% 80%, rgba(201,162,39,0.08) 0%, transparent 45%)",
          }}
        />
      ) : null}

      <header className="px-6 pt-8 pb-4 flex items-center justify-between relative z-10">
        <motion.button
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          onClick={onBack}
          className="w-10 h-10 rounded-full flex items-center justify-center"
          style={{
            background: "rgba(255, 255, 255, 0.08)",
            backdropFilter: "blur(20px)",
            border: "1px solid rgba(255, 255, 255, 0.1)",
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
          aria-label={c.back}
        >
          <ArrowLeft className="w-4 h-4" style={{ color: "#F5F5F7" }} strokeWidth={1.5} />
        </motion.button>

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

      <div className="flex-1 flex flex-col items-center justify-center px-8 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="w-full max-w-[280px]"
        >
          <h2 className="upload-title text-center">{u.heading}</h2>
          <p className="upload-subtitle text-center">{u.subtitle}</p>
          <MediaFileTrigger
            onFile={ingestFile}
            className="block mt-6 touch-manipulation"
          >
            <motion.div
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              className={`upload-card relative overflow-hidden aspect-square ${
                isDragging ? "drag-over scale-[1.02]" : ""
              } ${uploadedImage ? "rounded-[28px]" : ""}`}
              style={
                uploadedImage
                  ? { borderColor: "rgba(255, 255, 255, 0.2)" }
                  : undefined
              }
            >
              {uploadedImage ? (
                <>
                  {mediaType === "video" ? (
                    <div className="relative w-full h-full">
                      <video
                        ref={videoRef}
                        src={uploadedImage}
                        className="w-full h-full object-cover"
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
                    <img
                      src={imageForDisplay || uploadedImage}
                      alt=""
                      className="w-full h-full object-cover"
                      decoding="async"
                    />
                  )}
                  <div className="absolute top-3 right-3 w-8 h-8 rounded-full bg-[#c9a227] flex items-center justify-center">
                    <Check className="w-4 h-4 text-[#0a0a0a]" strokeWidth={3} />
                  </div>
                  <div className="absolute bottom-3 left-3 px-2 py-1 rounded-full bg-black/50 text-[10px] text-white">
                    {mediaType === "video" ? c.video : c.photo}
                  </div>
                </>
              ) : (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-5">
                  <div className="w-16 h-16 rounded-2xl flex items-center justify-center bg-[#c9a227]/10 border border-[#c9a227]/20">
                    <Upload className="w-7 h-7 text-[#c9a227]/70" strokeWidth={1.5} />
                  </div>
                  <div className="text-center">
                    <p className="text-sm font-light text-[#F5F5F7]">
                      {isDragging ? u.drop : u.drag}
                    </p>
                    <p className="text-xs mt-1.5 font-light text-[#A1A1A6]">{u.tapBrowse}</p>
                  </div>
                  <p className="upload-hint text-center text-[#A1A1A6]">{u.formats}</p>
                </div>
              )}
            </motion.div>
          </MediaFileTrigger>

          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.4 }}
            className="text-center text-[12px] mt-7 font-light leading-relaxed text-[#A1A1A6]"
          >
            {u.hint}
          </motion.p>
        </motion.div>
      </div>

      <div className="px-8 pb-10 relative z-10">
        <motion.button
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          onClick={onContinue}
          disabled={!uploadedImage}
          className="w-full py-4 rounded-2xl font-normal text-[15px] tracking-wider transition-all duration-300"
          style={{
            background: uploadedImage
              ? "linear-gradient(135deg, #b8860b 0%, #c9a227 30%, #d4af37 50%, #f5d77a 70%, #d4af37 100%)"
              : "rgba(255, 255, 255, 0.06)",
            color: uploadedImage ? "#0a0a0a" : "#A1A1A6",
            cursor: uploadedImage ? "pointer" : "not-allowed",
          }}
        >
          {u.continue}
        </motion.button>
      </div>
    </div>
  );
}
