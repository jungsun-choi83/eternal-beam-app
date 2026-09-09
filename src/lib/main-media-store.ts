/**
 * 고객이 올린 **메인 미디어** — 저장과 해석을 한 곳에서.
 *
 * ── 무엇이 어긋나 있었나 ────────────────────────────────────────────────────
 * 업로드 경로가 둘인데 하는 일이 달랐다.
 *
 *   홈 화면 선택기   상태 갱신 + 미디어 종류 + main_photo 저장 + 낡은 영상 URL 제거
 *   업로드 화면      상태 갱신 + 미디어 종류. **그게 전부다.**
 *
 * 그래서 업로드 화면으로 사진을 고르면 `eternal_beam_main_photo` 가 갱신되지
 * 않았다. 화면에는 방금 고른 사진이 보이는데(React 상태), localStorage 에는
 * 지난번 사진이 남아 있거나 아무것도 없었다.
 *
 * 그 값을 읽는 곳이 바로 **원본 배경**이다:
 *   * 테마 목록의 "원본 사진 그대로" 카드
 *   * 미리보기 화면의 배경
 *   * 장면 합성(= 유료 생성에 들어가는 그림)
 *
 * 결과는 세 가지였고 전부 조용했다: 카드가 검게 비거나, 지난번 사진이 배경으로
 * 들어가거나, 원본을 골랐는데 ORIGINAL_PHOTO_MISSING 으로 생성이 거절됐다.
 *
 * ── 규칙 ────────────────────────────────────────────────────────────────────
 * 미디어를 고르는 **모든** 경로가 `commitMainMedia` 하나만 부른다. 그 함수가
 * 종류·사진·영상 세 키를 한 번에 정합하게 만든다 — 사진을 넣으면 영상 URL 이
 * 사라지고, 영상을 넣으면 사진이 사라진다. 두 키가 동시에 살아 있으면 "지금
 * 올린 것이 무엇인가"에 답이 두 개가 된다.
 */

export const MAIN_PHOTO_KEY = "eternal_beam_main_photo";
export const MAIN_VIDEO_KEY = "eternal_beam_main_video_url";
export const MEDIA_TYPE_KEY = "eternal_beam_media_type";

export type MediaKind = "image" | "video";

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key)?.trim() || null;
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 용량 초과(큰 data: URL)는 일어난다. 화면은 React 상태로 계속 동작하고,
    // 여기서 던지면 업로드 자체가 실패한 것처럼 보인다.
  }
}

function safeRemove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/**
 * 방금 고른 미디어를 확정한다. **업로드 경로는 전부 이 함수를 통과한다.**
 *
 * 반대쪽 키를 지우는 것이 핵심이다. 사진을 새로 골랐는데 예전 영상 URL 이 남아
 * 있으면, 그 URL 은 이미 폐기된 blob: 이라 어디선가 조용히 깨진다.
 */
export function commitMainMedia(kind: MediaKind, url: string): void {
  const value = (url || "").trim();
  if (!value) return;

  safeSet(MEDIA_TYPE_KEY, kind);
  if (kind === "image") {
    safeSet(MAIN_PHOTO_KEY, value);
    safeRemove(MAIN_VIDEO_KEY);
  } else {
    safeSet(MAIN_VIDEO_KEY, value);
    safeRemove(MAIN_PHOTO_KEY);
  }
}

export function readMainPhoto(): string | null {
  return safeGet(MAIN_PHOTO_KEY);
}

export function readMainVideoUrl(): string | null {
  return safeGet(MAIN_VIDEO_KEY);
}

export function readMediaKind(): MediaKind | null {
  const v = safeGet(MEDIA_TYPE_KEY);
  return v === "image" || v === "video" ? v : null;
}

/**
 * 값 하나만 보고 종류를 추정한다 — 저장된 종류가 없을 때의 마지막 수단.
 *
 * blob: 은 이 앱에서 **영상 전용**이다(사진은 FileReader 의 data: URL 로 들어온다).
 * 이 구분이 없으면 영상 blob 이 "원본 사진"으로 취급돼, 정지 이미지로 그려야 할
 * 자리에 재생되지 않는 URL 이 들어간다.
 */
export function inferKindFromUrl(url: string | null | undefined): MediaKind | null {
  const v = (url || "").trim();
  if (!v) return null;
  if (v.startsWith("data:image/")) return "image";
  if (v.startsWith("data:video/") || v.startsWith("blob:")) return "video";
  if (/\.(mp4|webm|mov|m4v)(\?|#|$)/i.test(v)) return "video";
  if (/\.(jpe?g|png|webp|gif|heic|heif|avif)(\?|#|$)/i.test(v)) return "image";
  return null;
}

/**
 * "원본 사진 그대로" 배경에 쓸 **한 장.**
 *
 *   1. 지금 화면이 들고 있는 업로드 이미지 (가장 최신이고, 저장 실패와 무관하다)
 *   2. 저장된 eternal_beam_main_photo (새로고침 후 복원)
 *   3. 없음 → null. 호출부는 **오류를 보여 주고 멈춘다.**
 *
 * 영상은 원본 사진이 아니다. current 가 영상이면 건너뛰고 저장된 사진을 본다 —
 * 영상 URL 을 정지 배경으로 넘기면 검은 화면이 되고, 그 검은 화면이 그대로 유료
 * 생성에 들어간다.
 */
export function resolveOriginalPhoto(current?: string | null): string | null {
  const live = (current || "").trim();
  if (live && inferKindFromUrl(live) !== "video") return live;
  return readMainPhoto();
}
