import { getMemorialTheme } from "@/components/memorial/themes";
import {
  triggerThemeOnDevice,
  resolvePiHttpBase,
  isDevicePaired,
} from "@/lib/pi-sensor-bridge";

let lastSyncedThemeKey: string | null = null;
let debounceTimer: ReturnType<typeof setTimeout> | null = null;

/** QR 스플래시·데모 모드 등 기기 연동 대상인지 */
export function shouldSyncThemeToDevice(): boolean {
  if (typeof window === "undefined") return false;
  if (isDevicePaired()) return true;
  return resolvePiHttpBase() != null;
}

/**
 * 테마 선택 → Pi 터치스크린 배경 (fresh_forest = python/backgrounds/fresh_forest.mp4).
 * POST /demo/play { theme_id: "fresh_forest" } → UDP nfc_tagged → pi_display_bg.
 */
export function scheduleThemeBackgroundSync(themeId: number): void {
  if (!shouldSyncThemeToDevice()) return;

  const theme = getMemorialTheme(themeId);
  if (!theme || theme.requiresGeneration) return;

  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    debounceTimer = null;
    if (theme.themeKey === lastSyncedThemeKey) return;
    lastSyncedThemeKey = theme.themeKey;
    void triggerThemeOnDevice(theme.themeKey);
  }, 320);
}

/** 미리보기 완료 후 content_id 와 함께 재송출할 때 debounce 캐시 초기화 */
export function resetThemeBackgroundSyncCache(): void {
  lastSyncedThemeKey = null;
}
