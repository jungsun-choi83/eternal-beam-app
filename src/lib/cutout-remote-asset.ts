/**
 * 누끼의 **원격(스토리지) URL** 확보.
 *
 * 배경: 웹 플로우는 누끼를 `save_to_storage=false` 로 뽑는다
 * (ai-processing-screen). 그래서 브라우저는 data: URL 만 들고 있고,
 * 파이프라인의 dog_only_nobg_url 도 그 data: URL 로 시드된다.
 *
 * 백엔드는 그걸 가져올 수 없다. COME_CLOSER 제출이 stage="download" 로 실패한
 * 진짜 이유가 이것이었다 — 스토리지 장애가 아니라 애초에 원격 자산이 없었다.
 *
 * 여기서는 **재누끼를 하지 않는다**. 이미 만들어진 PNG 바이트를 1회 올리기만
 * 한다. idle 생성이 이미 돌았다면 그쪽이 같은 경로에 올려 두므로 대개 이 경로는
 * 타지 않는다.
 */

const PIPELINE_STORAGE_KEY = "eternal_beam_pipeline_v1";

export type CutoutPipelineLike = {
  content_id?: string;
  dog_only_nobg_url?: string | null;
  cutout_display_url?: string | null;
  [k: string]: unknown;
};

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 백엔드가 가져갈 수 있는 URL 인가 — credit_keyframe.is_remote_asset_url 과 같은 규칙. */
export function isRemoteAssetUrl(url?: string | null): boolean {
  const u = (url || "").trim().toLowerCase();
  return u.startsWith("http://") || u.startsWith("https://");
}

/**
 * 이미 만들어진 누끼 PNG(data: URL)를 스토리지에 1회 저장한다.
 * 실패해도 throw 하지 않는다 — 호출자는 null 을 보고 판단한다.
 */
export async function persistCutoutToStorage(params: {
  userId: string;
  contentId: string;
  dataUrl: string;
}): Promise<string | null> {
  const { userId, contentId, dataUrl } = params;
  if (!userId.trim() || !contentId.trim() || !dataUrl.trim()) return null;

  try {
    const res = await fetch(`${apiBase()}/api/assets/cutout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, content_id: contentId, data_url: dataUrl }),
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { cutout_url?: string | null };
    const url = body.cutout_url?.trim();
    return url && isRemoteAssetUrl(url) ? url : null;
  } catch {
    return null;
  }
}

/**
 * 파이프라인에서 COME_CLOSER 제출에 쓸 수 있는 원격 누끼 URL 을 얻는다.
 *
 *   이미 원격이면      → 그대로 (업로드하지 않는다)
 *   data: URL 뿐이면   → 1회 업로드 후 파이프라인에 병합
 *   둘 다 없으면       → null
 *
 * @returns url = 제출에 쓸 URL, pipeline = 갱신됐다면 새 파이프라인(아니면 null)
 */
export async function ensureRemoteCutoutUrl<T extends CutoutPipelineLike>(
  pipeline: T | null,
  params: { userId: string },
): Promise<{ url: string | null; pipeline: T | null }> {
  if (!pipeline) return { url: null, pipeline: null };

  const existing = pipeline.dog_only_nobg_url;
  if (isRemoteAssetUrl(existing)) return { url: existing!.trim(), pipeline: null };

  const contentId = (pipeline.content_id || "").trim();
  // data: 원본은 dog_only_nobg_url 이 우선이지만, 시드 시점에 따라
  // cutout_display_url 에만 남아 있을 수도 있다.
  const dataUrl = [existing, pipeline.cutout_display_url]
    .map((v) => (v || "").trim())
    .find((v) => v.toLowerCase().startsWith("data:"));

  if (!contentId || !dataUrl) return { url: null, pipeline: null };

  const url = await persistCutoutToStorage({ userId: params.userId, contentId, dataUrl });
  if (!url) return { url: null, pipeline: null };

  const next = { ...pipeline, dog_only_nobg_url: url } as T;
  try {
    sessionStorage.setItem(PIPELINE_STORAGE_KEY, JSON.stringify(next));
  } catch {
    /* quota — 메모리 상태만으로도 제출에는 충분하다 */
  }
  return { url, pipeline: next };
}
