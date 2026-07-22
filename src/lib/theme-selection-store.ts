import { getMemorialTheme, type MemorialTheme } from "@/components/memorial/themes";

export const THEME_ID_STORAGE_KEY = "eternal_beam_theme_id";
export const THEME_KEY_STORAGE_KEY = "eternal_beam_theme_key";

/** localStorage에 저장된 테마 id (테마 선택 화면·프리뷰 동기화용) */
export function readStoredThemeId(): number | null {
  try {
    const raw =
      localStorage.getItem(THEME_ID_STORAGE_KEY) ||
      localStorage.getItem("eternal_beam_background_theme_id");
    if (!raw) return null;
    const id = Number.parseInt(raw, 10);
    return Number.isFinite(id) && getMemorialTheme(id) ? id : null;
  } catch {
    return null;
  }
}

/** React state → localStorage 순으로 프리뷰에 쓸 테마 id 확정 */
export function resolveSelectedThemeId(selectedTheme: number | null): number | null {
  if (selectedTheme != null && getMemorialTheme(selectedTheme)) {
    return selectedTheme;
  }
  return readStoredThemeId();
}

/** FFmpeg/API preview·compose용 background_id — themes.ts themeKey 와 1:1 */
export function getThemeBackgroundApiId(theme: MemorialTheme): string {
  return theme.themeKey;
}
