/**
 * Pure file-selection validation for the Pet Upload / Intake screen.
 *
 * Extracted from photo-upload-screen.tsx so the accept/reject rules are
 * testable without rendering React, and so the screen can surface a real
 * reason to the person instead of silently dropping files.
 *
 * ── Why per-file, not "look at files[0]" ────────────────────────────────
 * The screen used to decide the whole batch's fate from `files[0]`'s kind —
 * if the very first pick was something `inferMediaKind` couldn't classify
 * (e.g. a PDF picked alongside two valid photos on Android), the entire
 * selection was dropped with no feedback, valid photos included. Classifying
 * every file independently fixes that without changing the existing
 * one-media-kind-per-pet rule (a video selection still wins the slot).
 */
import { inferMediaKind, type MediaKind } from "./media-file-kind.ts";

export const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;

export type IntakeRejectReason = "unsupported-type" | "oversized-image" | "oversized-video";

export type IntakeFileVerdict =
  | { file: File; kind: MediaKind; accepted: true }
  | { file: File; kind: MediaKind | null; accepted: false; reason: IntakeRejectReason };

export function classifyIntakeFile(
  file: File,
  limits: { maxImageBytes?: number; maxVideoBytes?: number } = {},
): IntakeFileVerdict {
  const maxImageBytes = limits.maxImageBytes ?? MAX_IMAGE_BYTES;
  const maxVideoBytes = limits.maxVideoBytes ?? MAX_VIDEO_BYTES;
  const kind = inferMediaKind(file);
  if (!kind) return { file, kind: null, accepted: false, reason: "unsupported-type" };
  if (kind === "image" && file.size > maxImageBytes) {
    return { file, kind, accepted: false, reason: "oversized-image" };
  }
  if (kind === "video" && file.size > maxVideoBytes) {
    return { file, kind, accepted: false, reason: "oversized-video" };
  }
  return { file, kind, accepted: true };
}

export type IntakeBatchResult = {
  /** null when nothing usable was picked. Video wins the slot when present. */
  kind: MediaKind | null;
  /** Accepted, size-checked image files, in selection order. Room is applied by the caller. */
  images: File[];
  /** First accepted video, if any — one media kind per pet, so extra videos are rejected. */
  video: File | null;
  rejected: Array<{ file: File; reason: IntakeRejectReason }>;
};

export function classifyIntakeBatch(
  files: File[],
  limits: { maxImageBytes?: number; maxVideoBytes?: number } = {},
): IntakeBatchResult {
  const rejected: Array<{ file: File; reason: IntakeRejectReason }> = [];
  const images: File[] = [];
  let video: File | null = null;

  for (const file of files) {
    const verdict = classifyIntakeFile(file, limits);
    if (!verdict.accepted) {
      rejected.push({ file, reason: verdict.reason });
      continue;
    }
    if (verdict.kind === "video") {
      if (!video) video = file;
      else rejected.push({ file, reason: "unsupported-type" });
      continue;
    }
    images.push(file);
  }

  return {
    kind: video ? "video" : images.length > 0 ? "image" : null,
    images,
    video,
    rejected,
  };
}

/** Splits accepted images at the remaining room for this pet's photo slots. */
export function clampToRoom(images: File[], room: number): { accepted: File[]; overflow: number } {
  const safeRoom = Math.max(0, room);
  return {
    accepted: images.slice(0, safeRoom),
    overflow: Math.max(0, images.length - safeRoom),
  };
}
