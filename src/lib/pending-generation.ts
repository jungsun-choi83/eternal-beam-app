/**
 * 누끼 완료 ~ 사용자 확인 사이에 "아직 생성하지 않은" 원본 해상도 누끼를 보관한다.
 *
 * 플로우가 바뀌면서(업로드 → 누끼 → 테마 → 미리보기 → **확인** → 생성) 누끼 시점과
 * 생성 시점이 분리됐다. 미리보기에서 확인을 누를 때 원본 해상도 누끼가 다시 필요하다.
 *
 * 보관 전략:
 *  - File 자체는 모듈 메모리에 둔다. SPA 라 화면 전환으로는 사라지지 않고,
 *    sessionStorage 용량(수 MB 데이터 URL)을 넘길 위험도 없다.
 *  - contentId 와 display URL 은 sessionStorage 에도 적어 둔다. 새로고침으로
 *    메모리가 날아가도 display URL 에서 File 을 되살릴 수 있다(해상도는 표시용).
 */

// 명시적 확장자 — Vite 도 node:test 도 그대로 해석한다(@/ 별칭은 Node 에서 못 푼다).
import { dataUrlToFile } from "./data-url-to-file.ts";

const PENDING_KEY = "eternal_beam_pending_cutout_v1";

export interface PendingCutoutMeta {
  contentId: string;
  /** 표시용 누끼 URL (data: 또는 http). 새로고침 복구용 폴백. */
  displayUrl: string;
}

/** 원본 해상도 누끼. 메모리에만 둔다(용량 때문에 sessionStorage 에 넣지 않음). */
let memoryFile: File | null = null;
let memoryMeta: PendingCutoutMeta | null = null;

export function setPendingCutout(
  file: File | null,
  contentId: string,
  displayUrl: string
): void {
  memoryFile = file;
  memoryMeta = { contentId, displayUrl };
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(memoryMeta));
  } catch {
    /* 용량 초과 등 — 메모리 사본만으로도 같은 세션에서는 동작한다 */
  }
}

export function getPendingCutoutMeta(): PendingCutoutMeta | null {
  if (memoryMeta) return memoryMeta;
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PendingCutoutMeta;
    if (!parsed?.contentId) return null;
    memoryMeta = parsed;
    return parsed;
  } catch {
    return null;
  }
}

/** 확인 시점에 생성 API 로 보낼 원본 해상도 누끼 File 을 되살린다. */
export async function rehydrateCutoutFile(): Promise<File | null> {
  if (memoryFile) return memoryFile;

  const meta = getPendingCutoutMeta();
  const url = meta?.displayUrl?.trim();
  if (!url) return null;

  if (url.startsWith("data:")) {
    try {
      return dataUrlToFile(url, "cutout.png");
    } catch {
      return null;
    }
  }

  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    const blob = await res.blob();
    const type = blob.type?.startsWith("image/") ? blob.type : "image/png";
    return new File([blob], "cutout.png", { type });
  } catch {
    return null;
  }
}

export function clearPendingCutout(): void {
  memoryFile = null;
  memoryMeta = null;
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* ignore */
  }
}

/** 테스트에서 모듈 메모리를 초기화하기 위한 훅. */
export function __resetPendingCutoutForTest(): void {
  memoryFile = null;
  memoryMeta = null;
}

// ---------------------------------------------------------------------------
// 실제 idle 영상 존재 여부 / devicePlay 진입 가드
// ---------------------------------------------------------------------------

const PIPELINE_KEY = "eternal_beam_pipeline_v1";

/**
 * sessionStorage 의 파이프라인 상태를 읽는다.
 * ai-processing-screen.tsx 의 ETERNAL_BEAM_PIPELINE_KEY 와 같은 키다 — 여기서
 * 다시 선언하는 이유는 컴포넌트를 import 하지 않고 라우팅 가드를 쓰기 위함이다.
 */
export function readStoredPipeline(): { idle_video_url?: string } | null {
  try {
    const raw = sessionStorage.getItem(PIPELINE_KEY);
    return raw ? (JSON.parse(raw) as { idle_video_url?: string }) : null;
  } catch {
    return null;
  }
}

/** 데모 mp4 는 실제 생성 결과가 아니다 — 기기 송출 자격이 없다. */
export function isDemoIdleUrl(url: string | null | undefined): boolean {
  const u = String(url ?? "").trim().toLowerCase();
  if (!u) return false;
  return u.includes("goya_idle") || u.includes("/demo/");
}

/** 파이프라인에 "진짜" idle 영상이 있는가. */
export function hasRealIdleVideo(
  pipeline: { idle_video_url?: string | null } | null | undefined
): boolean {
  const url = String(pipeline?.idle_video_url ?? "").trim();
  if (!url) return false;
  return !isDemoIdleUrl(url);
}

/**
 * devicePlay(기기 송출) 진입 가능 여부.
 * 실제 idle 영상이 없으면 막는다 — 단, 명시적 데모/테스트 경로는 예외.
 */
export function canEnterDevicePlay(
  pipeline: { idle_video_url?: string | null } | null | undefined,
  options?: { demo?: boolean }
): boolean {
  if (options?.demo) return true;
  return hasRealIdleVideo(pipeline);
}
