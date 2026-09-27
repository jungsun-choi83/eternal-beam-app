"use client";

import { useRef, useState } from "react";
import { motion } from "framer-motion";
import { X, Upload, CheckCircle2, XCircle, Loader2 } from "lucide-react";
import {
  generateIdleAnimationSet,
  IDLE_TEMPLATE_ORDER,
  type IdleTemplateKey,
  type IdleVariantResult,
} from "@/app/services/VideoGenerationService";
import type { CutoutResult } from "@/app/services/videoProcessingApi";

interface IdleGenerationTestPanelProps {
  userId: string;
  onClose: () => void;
}

type VariantStatus =
  | { state: "pending" }
  | { state: "running" }
  | { state: "done"; result: IdleVariantResult }
  | { state: "error"; message: string };

const TEMPLATE_LABELS: Record<IdleTemplateKey, string> = {
  IDLE_BREATH: "호흡(가슴 움직임)",
  IDLE_HEAD_TILT: "고개 갸웃",
  IDLE_TAIL_WAG: "꼬리 흔들기",
  IDLE_EAR_FLICK: "귀 움찔",
  IDLE_LOOK_AROUND: "주위 둘러보기",
};

function initialStatuses(): Record<IdleTemplateKey, VariantStatus> {
  const out = {} as Record<IdleTemplateKey, VariantStatus>;
  for (const key of IDLE_TEMPLATE_ORDER) out[key] = { state: "pending" };
  return out;
}

/**
 * 개발/테스트용 패널 — 사진 1장 → SAM2 누끼 → Luma 아이들 5종 세트를
 * VideoGenerationService.generateIdleAnimationSet()로 순차 생성해서 바로 확인.
 * 실제 과금이 발생하므로 VITE_ENABLE_LUMA=1일 때만 설정 화면에 노출됨.
 */
export function IdleGenerationTestPanel({ userId, onClose }: IdleGenerationTestPanelProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [cutout, setCutout] = useState<CutoutResult | null>(null);
  const [statuses, setStatuses] = useState<Record<IdleTemplateKey, VariantStatus>>(initialStatuses);
  const [busy, setBusy] = useState(false);
  const [globalError, setGlobalError] = useState<string | null>(null);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setPhotoFile(file);
    setPhotoPreview(URL.createObjectURL(file));
    setCutout(null);
    setStatuses(initialStatuses());
    setGlobalError(null);
  };

  const run = async () => {
    if (!photoFile || busy) return;
    setBusy(true);
    setGlobalError(null);
    setStatuses(initialStatuses());

    try {
      await generateIdleAnimationSet(photoFile, {
        userId,
        maxRetries: 2,
        onCutoutComplete: (c: CutoutResult) => setCutout(c),
        onVariantStart: (templateKey: IdleTemplateKey) => {
          setStatuses((prev) => ({ ...prev, [templateKey]: { state: "running" } }));
        },
        onVariantComplete: (result: IdleVariantResult) => {
          setStatuses((prev) => ({
            ...prev,
            [result.template_key]: { state: "done", result },
          }));
        },
        onVariantError: (templateKey: IdleTemplateKey, message: string) => {
          setStatuses((prev) => ({
            ...prev,
            [templateKey]: { state: "error", message },
          }));
        },
      });
    } catch (e) {
      setGlobalError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const cutoutDisplayUrl =
    cutout?.cutout_url || (cutout?.cutout_png_base64 ? `data:image/png;base64,${cutout.cutout_png_base64}` : null);

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      className="eb-card eb-card--muted mx-2 mb-4 p-4"
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        <p className="eb-section-title truncate">아이들(Idle) 5종 세트 테스트</p>
        <button type="button" onClick={onClose} className="mem-icon-btn -mr-2 shrink-0" aria-label="Close">
          <X className="w-4 h-4 text-[var(--eb-text-2)]" />
        </button>
      </div>

      <p className="eb-caption mb-3">
        사진 1장 → SAM2 누끼 → Luma로 아이들 모션 5종을 순차 생성합니다. 실제 Luma 과금이 발생할 수 있어요
        (LUMA_MOCK=1이면 무료로 파이프라인만 확인).
      </p>

      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="flex flex-wrap items-center gap-3 mb-3">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy}
          className="eb-btn eb-btn--secondary mem-btn-secondary text-xs px-3"
        >
          <Upload className="w-3.5 h-3.5" />
          사진 선택
        </button>

        {photoPreview ? (
          <img
            src={photoPreview}
            alt="original"
            className="w-10 h-10 rounded-[var(--eb-radius-xs)] object-cover border border-[var(--eb-hairline)]"
          />
        ) : null}

        {cutoutDisplayUrl ? (
          <img
            src={cutoutDisplayUrl}
            alt="cutout"
            className="w-10 h-10 rounded-[var(--eb-radius-xs)] object-cover bg-[var(--eb-surface-inverse)] border border-[var(--eb-gold-line)]"
          />
        ) : null}

        <button
          type="button"
          onClick={() => void run()}
          disabled={!photoFile || busy}
          aria-busy={busy || undefined}
          className="eb-btn eb-btn--primary mem-btn-primary ml-auto text-xs px-3"
        >
          {busy ? "생성 중…" : "5종 생성 시작"}
        </button>
      </div>

      <div className="space-y-2">
        {IDLE_TEMPLATE_ORDER.map((key: IdleTemplateKey) => {
          const status = statuses[key];
          return (
            <div
              key={key}
              className="flex items-center gap-3 p-2 min-h-[var(--eb-touch)] rounded-[var(--eb-radius-sm)] bg-[var(--eb-surface)] border border-[var(--eb-hairline)]"
            >
              <div className="w-6 shrink-0 flex items-center justify-center">
                {status.state === "pending" ? (
                  <span className="eb-status-dot eb-status-dot--neutral" />
                ) : status.state === "running" ? (
                  <Loader2 className="w-4 h-4 animate-spin text-[var(--eb-gold-text)]" />
                ) : status.state === "done" ? (
                  <CheckCircle2
                    className={`w-4 h-4 ${
                      status.result.is_black_background
                        ? "text-[var(--eb-sage-text)]"
                        : "text-[var(--eb-warn-text)]"
                    }`}
                  />
                ) : (
                  <XCircle className="w-4 h-4 text-[var(--eb-terracotta-text)]" />
                )}
              </div>

              <div className="flex-1 min-w-0">
                <p className="text-[12px] text-[var(--eb-text)] truncate">{TEMPLATE_LABELS[key]}</p>
                {status.state === "done" ? (
                  <p className="eb-caption text-[11px]">
                    {status.result.is_black_background ? "블랙 배경 OK" : "블랙 배경 아님(경고)"} · 재시도{" "}
                    {status.result.retries_used}회
                    {status.result.background_luminance != null
                      ? ` · 밝기 ${status.result.background_luminance.toFixed(0)}`
                      : ""}
                  </p>
                ) : status.state === "error" ? (
                  <p className="eb-field-error text-[11px] truncate">{status.message}</p>
                ) : null}
              </div>

              {status.state === "done" ? (
                <video
                  src={status.result.video_url}
                  className="w-14 h-14 rounded-[var(--eb-radius-xs)] object-cover shrink-0 bg-[var(--eb-surface-inverse)]"
                  muted
                  loop
                  autoPlay
                  playsInline
                />
              ) : null}
            </div>
          );
        })}
      </div>

      {globalError ? (
        <p className="mt-3 eb-field-error" role="alert">
          {globalError}
        </p>
      ) : null}
    </motion.div>
  );
}
