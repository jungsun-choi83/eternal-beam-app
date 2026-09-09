/**
 * "내 사진으로 나만의 배경 만들기"(custom_photo_bg) 결과 저장/조회.
 *
 * 이 테마는 themes.ts에 고정 bgVideo가 없다(사용자마다 다른 영상이 생성되므로).
 * 생성이 끝나면 결과 mp4 URL을 여기 정의된 localStorage 키에 저장하고, 프리뷰
 * 화면들(theme-selection-screen.tsx, preview-screen.tsx)은 getEffectiveBgVideo()로
 * "고정 bgVideo가 있으면 그걸, 없고 custom_photo_bg면 저장된 생성 결과를" 가져온다.
 */

// 값 import 라 상대 경로를 쓴다 — `@/` 별칭은 Vite 만 풀고 node --test 는 못 푼다.
// (동작은 동일하다: tsconfig 의 allowImportingTsExtensions 덕에 양쪽 다 해석된다.)
import {
  CUSTOM_PHOTO_BG_THEME_KEY,
  getThemePreviewBgVideo,
  type MemorialTheme,
} from "../components/memorial/themes.ts";

export const CUSTOM_BG_VIDEO_URL_KEY = "eternal_beam_custom_bg_video_url";
export const CUSTOM_BG_JOB_ID_KEY = "eternal_beam_custom_bg_job_id";
export const CUSTOM_BG_CONTENT_ID_KEY = "eternal_beam_custom_bg_content_id";

export function isCustomPhotoBgTheme(theme: Pick<MemorialTheme, "themeKey"> | null | undefined): boolean {
  return theme?.themeKey === CUSTOM_PHOTO_BG_THEME_KEY;
}

/** 테마의 실제 프리뷰 배경 영상 — 고정 테마는 theme.bgVideo, custom_photo_bg는 생성 결과. */
export function getEffectiveBgVideo(theme: MemorialTheme | null | undefined): string | undefined {
  if (!theme) return undefined;
  const fixed = getThemePreviewBgVideo(theme);
  if (fixed) return fixed;
  if (isCustomPhotoBgTheme(theme)) {
    return getStoredCustomBgVideoUrl() ?? undefined;
  }
  return undefined;
}

export function getStoredCustomBgVideoUrl(): string | null {
  try {
    return localStorage.getItem(CUSTOM_BG_VIDEO_URL_KEY);
  } catch {
    return null;
  }
}

export function setStoredCustomBgVideoUrl(url: string): void {
  try {
    localStorage.setItem(CUSTOM_BG_VIDEO_URL_KEY, url);
  } catch {
    /* ignore */
  }
}

export function clearStoredCustomBgVideoUrl(): void {
  try {
    localStorage.removeItem(CUSTOM_BG_VIDEO_URL_KEY);
    localStorage.removeItem(CUSTOM_BG_JOB_ID_KEY);
  } catch {
    /* ignore */
  }
}
