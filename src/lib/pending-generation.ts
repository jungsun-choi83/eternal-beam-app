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

export const DEFAULT_PET_SLOT_ID = "pet_slot_1";

/**
 * 원본 해상도 누끼. 메모리에만 둔다(용량 때문에 sessionStorage 에 넣지 않음).
 *
 * `memoryFile` 은 **활성 펫**의 것이다. 펫을 바꿀 때 지금 것을 슬롯 보관함에
 * 넣고 대상 슬롯의 것을 꺼낸다 — 예전에는 칸이 하나뿐이라, 펫 2를 처리한 뒤
 * 펫 1로 돌아가 생성하면 펫 2의 누끼 File 이 그대로 올라갔다.
 */
let memoryFile: File | null = null;
let memoryMeta: PendingCutoutMeta | null = null;
let activeSlotId = DEFAULT_PET_SLOT_ID;
const filesBySlot = new Map<string, File>();

export function setPendingCutout(
  file: File | null,
  contentId: string,
  displayUrl: string,
  slotId: string = activeSlotId
): void {
  activeSlotId = slotId || DEFAULT_PET_SLOT_ID;
  memoryFile = file;
  if (file) filesBySlot.set(activeSlotId, file);
  else filesBySlot.delete(activeSlotId);
  memoryMeta = { contentId, displayUrl };
  try {
    sessionStorage.setItem(PENDING_KEY, JSON.stringify(memoryMeta));
  } catch {
    /* 용량 초과 등 — 메모리 사본만으로도 같은 세션에서는 동작한다 */
  }
}

/**
 * 펫 전환 — 활성 누끼 File 을 갈아 끼운다.
 *
 * 메타(contentId/displayUrl)는 **읽지 않는다**: sessionStorage 쪽 투영은
 * pet-slot-state 가 이미 바꿔 놓았으므로, 캐시만 비워 다음 읽기가 새 값을
 * 집게 한다.
 */
export function activatePendingCutoutSlot(slotId: string): void {
  const next = slotId || DEFAULT_PET_SLOT_ID;
  if (memoryFile) filesBySlot.set(activeSlotId, memoryFile);
  activeSlotId = next;
  memoryFile = filesBySlot.get(next) ?? null;
  memoryMeta = null;
}

export function getActivePendingCutoutSlotId(): string {
  return activeSlotId;
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

/** 슬롯을 지정하면 그 칸만, 지정하지 않으면 활성 칸을 지운다. */
export function clearPendingCutout(slotId?: string): void {
  filesBySlot.delete(slotId || activeSlotId);
  if (slotId && slotId !== activeSlotId) return;
  memoryFile = null;
  memoryMeta = null;
  try {
    sessionStorage.removeItem(PENDING_KEY);
  } catch {
    /* ignore */
  }
}

/** 리셋·로그아웃 — 모든 펫의 보관 누끼를 버린다. */
export function clearAllPendingCutouts(): void {
  filesBySlot.clear();
  activeSlotId = DEFAULT_PET_SLOT_ID;
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
  filesBySlot.clear();
  activeSlotId = DEFAULT_PET_SLOT_ID;
  memoryFile = null;
  memoryMeta = null;
}

// ---------------------------------------------------------------------------
// 실제 idle 영상 존재 여부 / devicePlay 진입 가드
// ---------------------------------------------------------------------------

const PIPELINE_KEY = "eternal_beam_pipeline_v1";

/**
 * 저장된 파이프라인의 **읽기용 모양.**
 *
 * ai-processing-screen.tsx 의 `StoredPipeline` 과 같은 JSON 이지만, 그 타입은
 * 컴포넌트 모듈에 있다 — 라우팅 가드가 화면을 import 하지 않기 위해 여기서
 * 따로 적는다. 저장된 값은 예전 세션에서 온 것일 수 있으므로 **모든 필드가
 * optional** 이다. 예전에는 반환 타입이 `{ idle_video_url?: string }` 뿐이라
 * `.content_id` 를 읽는 호출부가 타입 오류로 남아 있었다.
 */
export interface StoredPipelineSnapshot {
  content_id?: string;
  cutout_display_url?: string;
  dog_only_nobg_url?: string;
  idle_video_url?: string | null;
  action_video_url?: string | null;
  come_closer_video_url?: string | null;
  background_baked?: boolean;
  delivery_format?: string | null;
  generation_source?: string | null;
  qa_decision?: string | null;
  scene_id?: string | null;
  phase1_intake?: {
    status?: "ready";
    pet_id?: string;
    original_reference_id?: string;
    cutout_reference_id?: string;
    original_reference_ids?: string[];
    cutout_reference_ids?: string[];
  } | null;
}

/**
 * sessionStorage 의 파이프라인 상태를 읽는다.
 * ai-processing-screen.tsx 의 ETERNAL_BEAM_PIPELINE_KEY 와 같은 키다 — 여기서
 * 다시 선언하는 이유는 컴포넌트를 import 하지 않고 라우팅 가드를 쓰기 위함이다.
 */
export function readStoredPipeline(): StoredPipelineSnapshot | null {
  try {
    const raw = sessionStorage.getItem(PIPELINE_KEY);
    return raw ? (JSON.parse(raw) as StoredPipelineSnapshot) : null;
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
