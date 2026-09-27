"use client";

import { useState, useCallback, useRef, useEffect, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Upload, Check, Play, Pause, X, Pencil, AlertCircle, Plus, ArrowLeft } from "lucide-react";
import { HologramEffects } from "./hologram-effects";
import { createDisplayImageUrl } from "@/lib/display-image";
import { memorialT } from "@/components/memorial/memorial-i18n";
import { MediaFileTrigger } from "@/components/memorial/media-file-trigger";
import { PhotoUploadGuide } from "@/components/memorial/photo-upload-guide";
import { CutoutStage } from "@/components/memorial/cutout-stage";
import { MemorialIconButton, MemorialPrimaryButton } from "@/components/memorial/memorial-chrome";
import type { MediaKind } from "@/lib/main-media-store";
import { classifyIntakeBatch, clampToRoom, type IntakeRejectReason } from "@/lib/photo-intake-validation";
import { CUTOUT_WARMUP_MAX_MS } from "@/lib/cutout-speed-mode";
import { warmupVideoApi } from "@/lib/video-api-warmup";
import { traceImage } from "@/lib/image-trace"; // [IMAGE-TRACE]

interface PhotoUploadScreenProps {
  uploadedImage: string | null;
  uploadedImages?: string[];
  canStart?: boolean;
  /**
   * 활성 펫의 미디어 종류. **부모가 내려 준다.**
   *
   * 예전에는 이 화면이 `eternal_beam_media_type` 을 직접 읽었다. 그 칸은 앱
   * 전체에 하나뿐이라 언제나 "마지막으로 만진 펫"의 답이었고, 직전 펫이 영상이면
   * 방금 고른 새 펫의 사진이 재생되지 않는 <video> 로 그려졌다.
   */
  mediaType?: MediaKind | null;
  petSlotsCount?: number;
  activePetSlotIndex?: number;
  maxPetSlots?: number;
  onSelectPetSlot?: (index: number) => void;
  onAddPetSlot?: () => void;
  language?: string;
  /**
   * 고른 미디어를 부모에게 넘긴다.
   *
   * ⚠️ **kind 를 반드시 함께 넘긴다.** 이 화면은 File.type 을 본 유일한 지점이라
   * 종류를 확실히 아는 곳도 여기뿐이다. URL 만 넘기면 부모가 문자열 모양으로
   * 추측해야 하고, 그 추측이 틀리면 영상이 "원본 사진"으로 저장된다.
   */
  onImageUpload: (imageUrl: string, kind: MediaKind) => void;
  onImagesUpload?: (files: File[]) => void;
  onRemoveImage?: (index: number) => void;
  /** 사진 한 장을 새 파일로 교체한다 — 같은 자리, 같은 신원, 새 내용. */
  onReplaceImage?: (index: number, file: File) => void;
  imageStates?: Array<{ status: "pending" | "uploading" | "success" | "error"; error?: string | null }>;
  maxImages?: number;
  onContinue: () => void;
  onBack: () => void;
}

export function PhotoUploadScreen({
  uploadedImage,
  uploadedImages,
  canStart,
  mediaType = null,
  petSlotsCount = 1,
  activePetSlotIndex = 0,
  maxPetSlots = 3,
  onSelectPetSlot,
  onAddPetSlot,
  language = "ko",
  onImageUpload,
  onImagesUpload,
  onRemoveImage,
  onReplaceImage,
  imageStates,
  maxImages = 3,
  onContinue,
  onBack,
}: PhotoUploadScreenProps) {
  const m = memorialT(language);
  const u = m.upload;
  const c = m.common;
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewUrls, setPreviewUrls] = useState<string[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const replaceIndexRef = useRef<number | null>(null);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
  }, []);

  const isVideo = mediaType === "video";

  /** 사진만. 영상 슬롯의 blob: URL 이 사진 그리드로 새어 들어가지 않는다. */
  const selectedImages = useMemo(() => {
    if (isVideo) return [];
    if (uploadedImages && uploadedImages.length > 0) {
      return uploadedImages;
    }

    return uploadedImage ? [uploadedImage] : [];
  }, [isVideo, uploadedImages, uploadedImage]);

  const rejectReasonMessage = useCallback(
    (reason: IntakeRejectReason) => {
      if (reason === "oversized-image") return u.errors.oversizedImage;
      if (reason === "oversized-video") return u.errors.oversizedVideo;
      return u.errors.unsupportedType;
    },
    [u],
  );

  const ingestFiles = useCallback(
    (files: File[]) => {
      if (!files.length) return;
      const { kind, images, video: file, rejected } = classifyIntakeBatch(files);
      const messages = new Set(rejected.map((r) => rejectReasonMessage(r.reason)));

      if (kind === "video" && file) {
        setFeedback(messages.size > 0 ? Array.from(messages).join(" ") : null);
        onImageUpload(URL.createObjectURL(file), "video");
        return;
      }

      if (images.length === 0) {
        setFeedback(messages.size > 0 ? Array.from(messages).join(" ") : null);
        return;
      }

      const room = Math.max(0, maxImages - (isVideo ? 0 : selectedImages.length));
      const { accepted, overflow } = clampToRoom(images, room);
      if (overflow > 0) messages.add(u.errors.tooMany(maxImages));
      setFeedback(messages.size > 0 ? Array.from(messages).join(" ") : null);
      if (accepted.length === 0) return;

      void warmupVideoApi({ coldStart: true, maxWaitMs: CUTOUT_WARMUP_MAX_MS });
      for (const file of accepted) {
        // [IMAGE-TRACE] OS/브라우저 파일 선택기가 넘겨준 그대로의 File.
        void traceImage("file-selected (picker)", file, "original-upload", "kind=image");
      }

      if (onImagesUpload) {
        onImagesUpload(accepted);
        return;
      }

      const first = accepted[0];
      if (!first) return;
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        void traceImage("state:uploadedImage", result, "original-upload");
        onImageUpload(result, "image");
      };
      reader.readAsDataURL(first);
    },
    [isVideo, maxImages, onImageUpload, onImagesUpload, rejectReasonMessage, selectedImages.length, u],
  );

  const ingestReplacement = useCallback(
    (files: File[]) => {
      const index = replaceIndexRef.current;
      replaceIndexRef.current = null;
      if (index === null || !files.length || !onReplaceImage) return;
      const { images, rejected } = classifyIntakeBatch(files.slice(0, 1));
      if (rejected.length > 0 || images.length === 0) {
        setFeedback(rejectReasonMessage(rejected[0]?.reason ?? "unsupported-type"));
        return;
      }
      setFeedback(null);
      onReplaceImage(index, images[0]);
    },
    [onReplaceImage, rejectReasonMessage],
  );

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);

      const files = Array.from(e.dataTransfer.files ?? []);
      if (files.length > 0) ingestFiles(files);
    },
    [ingestFiles],
  );

  const hasMedia = selectedImages.length > 0 || (isVideo && Boolean(uploadedImage));
  // 영상은 누끼 인테이크 대상이 아니다 — 부모가 canStart 로 막는다.
  const isStartEnabled = canStart ?? selectedImages.length > 0;

  useEffect(() => {
    const first = selectedImages[0] ?? null;
    if (!first?.startsWith("data:image/")) {
      setPreviewUrl(first);
      return;
    }
    let cancelled = false;
    createDisplayImageUrl(first, 480).then((url) => {
      if (!cancelled) setPreviewUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [selectedImages]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const results = await Promise.all(
        selectedImages.map(async (url) => {
          if (!url?.startsWith("data:image/")) return url;
          try {
            return await createDisplayImageUrl(url, 240);
          } catch {
            return url;
          }
        }),
      );
      if (!cancelled) setPreviewUrls(results);
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedImages]);

  // 펫을 바꾸면 지난 펫의 에러 배너가 새 펫 화면에 남지 않는다.
  useEffect(() => {
    setFeedback(null);
  }, [activePetSlotIndex]);

  const imageForDisplay = previewUrl || selectedImages[0] || uploadedImage;

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
  const canAddPet = petSlotsCount < Math.max(1, maxPetSlots);

  const statusLabel = (state?: { status: "pending" | "uploading" | "success" | "error"; error?: string | null }) => {
    if (!state) return null;
    if (state.status === "uploading") return u.uploadingStatus;
    if (state.status === "success") return u.uploadedStatus;
    if (state.status === "error") return state.error || u.uploadErrorStatus;
    return u.pendingStatus;
  };

  return (
    <div className="memorial-screen h-full flex flex-col relative overflow-hidden">
      <HologramEffects />

      <header className="pet-intake__header px-6 relative z-10 shrink-0">
        <div className="pet-intake__header-inner">
          <MemorialIconButton
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            onClick={onBack}
            aria-label={c.back}
            className="eb-back-btn"
          >
            <ArrowLeft className="h-5 w-5" aria-hidden />
          </MemorialIconButton>

          <div className="flex-1 min-w-0 text-center">
            <p className="memorial-eyebrow eb-eyebrow mb-1">01 · Photo</p>
            <h1 className="upload-title eb-title m-0 leading-tight">{u.heading}</h1>
          </div>

          <div className="pet-intake__header-spacer" aria-hidden />
        </div>
      </header>

      <div className="pet-intake__scroll flex-1 min-h-0 overflow-y-auto hide-scrollbar px-6 py-1 relative z-10">
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.2 }}
          className="pet-intake__layout relative z-10"
        >
          <div className="pet-intake__rail">
            <p className="upload-subtitle eb-body-sm text-center mt-0 mb-0">{u.subtitle}</p>

            <div className="pet-intake__tabs" role="tablist" aria-label="Pet slots">
              {Array.from({ length: petSlotsCount }).map((_, index) => {
                const active = index === activePetSlotIndex;
                const ready = index === activePetSlotIndex ? hasMedia : undefined;
                return (
                  <button
                    key={`pet-slot-${index}`}
                    type="button"
                    role="tab"
                    aria-selected={active}
                    onClick={() => onSelectPetSlot?.(index)}
                    className={`pet-intake__tab${active ? " pet-intake__tab--active" : ""}${
                      ready ? " pet-intake__tab--ready" : ""
                    }`}
                  >
                    {active && hasMedia ? (
                      <Check className="w-3.5 h-3.5" strokeWidth={2.5} aria-hidden />
                    ) : null}
                    {u.petTabLabel(index + 1)}
                  </button>
                );
              })}
              {onAddPetSlot ? (
                <button
                  type="button"
                  onClick={onAddPetSlot}
                  disabled={!canAddPet}
                  className="pet-intake__add-tab"
                  aria-label={canAddPet ? u.addPet : u.addPetLimitReached}
                  title={canAddPet ? u.addPet : u.addPetLimitReached}
                >
                  <Plus className="w-3.5 h-3.5" strokeWidth={2.5} aria-hidden />
                  {u.addPet}
                </button>
              ) : null}
            </div>

            <div className="pet-intake__desktop-only">
              <PhotoUploadGuide language={language} />
              <p className="memorial-body eb-caption leading-relaxed mt-4">{u.hint}</p>
            </div>
          </div>

          <div className="pet-intake__stage">
            {!isVideo ? (
              <div className="pet-intake__progress" aria-live="polite">
                {Array.from({ length: maxImages }).map((_, i) => (
                  <span
                    key={`progress-${i}`}
                    className={`pet-intake__progress-dot${i < selectedImages.length ? " is-filled" : ""}`}
                    aria-hidden
                  />
                ))}
                <span className="pet-intake__progress-label">
                  {u.photoCountLabel(selectedImages.length, maxImages)}
                </span>
              </div>
            ) : null}

            <AnimatePresence>
              {feedback ? (
                <motion.div
                  initial={{ opacity: 0, y: -6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  className="pet-intake__banner pet-intake__banner--error"
                  role="alert"
                >
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden />
                  <span className="flex-1">{feedback}</span>
                  <button
                    type="button"
                    className="pet-intake__banner-dismiss"
                    onClick={() => setFeedback(null)}
                    aria-label={u.errors.dismiss}
                  >
                    <X className="w-4 h-4" aria-hidden />
                  </button>
                </motion.div>
              ) : null}
            </AnimatePresence>

            <MediaFileTrigger
              onFiles={ingestFiles}
              multiple
              disabled={!isVideo && selectedImages.length >= maxImages}
              className="block touch-manipulation"
            >
              <motion.div
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                className={`upload-card relative overflow-hidden pet-intake__dropzone ${
                  isDragging ? "drag-over" : ""
                }`}
              >
                {hasMedia ? (
                  <>
                    {isVideo ? (
                      <div className="relative w-full h-full cutout-stage cutout-stage--fill">
                        <video
                          ref={videoRef}
                          src={uploadedImage ?? undefined}
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
                          className="absolute inset-0 flex items-center justify-center"
                        >
                          {/* Media overlay: dark translucent chip on top of the video (allowed). */}
                          <div className="pet-intake__play glass-dark">
                            {isPlaying ? (
                              <Pause className="w-6 h-6" fill="currentColor" aria-hidden />
                            ) : (
                              <Play className="w-6 h-6 ml-1" fill="currentColor" aria-hidden />
                            )}
                          </div>
                        </button>
                      </div>
                    ) : (
                      <CutoutStage className="w-full h-full" fit="cover">
                        <img
                          src={imageForDisplay || selectedImages[0]}
                          alt=""
                          className="cutout-stage__subject"
                          decoding="async"
                        />
                      </CutoutStage>
                    )}
                    <div className="eb-check-mark pet-intake__media-check absolute top-3 right-3 z-10" aria-hidden>
                      <Check className="w-4 h-4" strokeWidth={3} />
                    </div>
                    {/* Media overlay chip on top of the photo/video (dark on media is allowed). */}
                    <div className="pet-intake__media-kind glass-dark absolute bottom-3 left-3 rounded-full">
                      {isVideo ? c.video : c.photo}
                    </div>
                  </>
                ) : (
                  <div className="absolute inset-0 flex flex-col items-center justify-center gap-4 px-6 py-8">
                    <div className="upload-card__empty-icon">
                      <Upload className="w-6 h-6" strokeWidth={1.5} aria-hidden />
                    </div>
                    <div className="text-center">
                      <p className="eb-body font-medium leading-snug">
                        {isDragging ? u.drop : u.drag}
                      </p>
                      <p className="memorial-caption eb-caption mt-2">{u.tapBrowse}</p>
                      <p className="pet-intake__desktop-only memorial-caption eb-caption mt-1">
                        {u.dragDropHint}
                      </p>
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

            {selectedImages.length > 0 && !isVideo ? (
              <div className="pet-intake__grid">
                {selectedImages.map((url, index) => {
                  const state = imageStates?.[index];
                  const label = statusLabel(state);
                  return (
                    <div key={`${url}_${index}`} className="pet-intake__thumb">
                      <img
                        src={previewUrls[index] || url}
                        alt=""
                        decoding="async"
                      />
                      <div className="pet-intake__thumb-actions">
                        {onReplaceImage ? (
                          <MediaFileTrigger
                            onFiles={(files) => {
                              replaceIndexRef.current = index;
                              ingestReplacement(files);
                            }}
                            className="pet-intake__thumb-btn"
                          >
                            <span
                              className="w-full h-full flex items-center justify-center"
                              aria-label={u.replaceImageAria(index + 1)}
                              role="button"
                            >
                              <Pencil className="w-3.5 h-3.5" aria-hidden />
                            </span>
                          </MediaFileTrigger>
                        ) : null}
                        {onRemoveImage ? (
                          <button
                            type="button"
                            aria-label={u.removeImageAria(index + 1)}
                            onClick={() => onRemoveImage(index)}
                            className="pet-intake__thumb-btn"
                          >
                            <X className="w-4 h-4" aria-hidden />
                          </button>
                        ) : null}
                      </div>
                      {label ? <div className="pet-intake__thumb-status">{label}</div> : null}
                    </div>
                  );
                })}
              </div>
            ) : null}

            <details className="pet-intake__mobile-only pet-intake__tips">
              <summary className="pet-intake__tips-summary">{u.hint}</summary>
              <div className="pet-intake__tips-body">
                <PhotoUploadGuide language={language} />
              </div>
            </details>
          </div>
        </motion.div>
      </div>

      <div className="pet-intake__footer px-6 relative z-10 shrink-0">
        <div className="pet-intake__footer-inner">
          <MemorialPrimaryButton
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            onClick={onContinue}
            disabled={!isStartEnabled}
          >
            {u.continue}
          </MemorialPrimaryButton>
          {!isStartEnabled ? <p className="pet-intake__readiness">{u.readinessHint}</p> : null}
        </div>
      </div>
    </div>
  );
}
