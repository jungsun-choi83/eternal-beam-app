/**
 * "내 사진으로 나만의 배경 만들기"(custom_photo_bg) — /api/background-video/* 클라이언트.
 *
 * live-portrait 큐(backend/routers/live_portrait.py)와 동일한 "등록만 하고 로컬
 * GPU 워커가 폴링해서 처리" 구조 — 여기서도 enqueue는 즉시 반환되고, 실제 진행은
 * getCustomBackgroundJobStatus()로 폴링해서 확인한다.
 */

import { getVideoApiBaseUrl } from "@/app/services/videoProcessingApi";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";

export interface CreateBackgroundVideoJobResult {
  job_id: string;
  status: string;
}

export type BackgroundVideoJobStatus = "queued" | "running" | "done" | "failed";

export interface BackgroundVideoJobStatusResult {
  job_id: string;
  status: BackgroundVideoJobStatus;
  progress: { stage?: string; detail?: string | null; updated_at?: string };
  result_video_url?: string | null;
  result_meta?: Record<string, unknown> | null;
  error?: string | null;
}

async function parseJsonOrThrow<T>(res: Response, fallbackMessage: string): Promise<T> {
  let body: Record<string, unknown> = {};
  try {
    body = (await res.json()) as Record<string, unknown>;
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : fallbackMessage;
    throw new Error(detail);
  }
  return body as T;
}

/** 원본 사진(File) 1장 → 배경 애니메이션 생성 잡 등록. 즉시 job_id 반환(비동기 처리). */
export async function enqueueCustomBackgroundJob(
  photoFile: File,
  opts: { contentId?: string } = {}
): Promise<CreateBackgroundVideoJobResult> {
  const base = getVideoApiBaseUrl();
  const form = new FormData();
  form.append("file", photoFile);
  form.append("user_id", getEternalBeamUserId());
  if (opts.contentId) form.append("content_id", opts.contentId);

  const res = await fetch(`${base}/api/background-video/generate`, {
    method: "POST",
    body: form,
  });
  return parseJsonOrThrow<CreateBackgroundVideoJobResult>(
    res,
    "배경 생성 요청에 실패했습니다."
  );
}

export async function getCustomBackgroundJobStatus(
  jobId: string
): Promise<BackgroundVideoJobStatusResult> {
  const base = getVideoApiBaseUrl();
  const res = await fetch(`${base}/api/background-video/jobs/${encodeURIComponent(jobId)}`);
  return parseJsonOrThrow<BackgroundVideoJobStatusResult>(
    res,
    "배경 생성 상태를 확인할 수 없습니다."
  );
}
