/**
 * 포레스트 체험 화면 에셋 — 배경 mp4는 추후 교체 예정.
 * 1) public/demo/forest.mp4 파일만 덮어쓰기 (기본)
 * 2) 또는 VITE_FOREST_BG_URL 로 URL 지정
 */

export const FOREST_THEME_ID = 8
export const FOREST_THEME_KEY = 'fresh_forest'

export const forestDemoAssets = {
  background:
    import.meta.env.VITE_FOREST_BG_URL?.trim() || '/demo/forest.mp4',
  idle:
    import.meta.env.VITE_FOREST_IDLE_URL?.trim() || '/demo/goya_idle_packed.mp4',
  action:
    import.meta.env.VITE_FOREST_ACTION_URL?.trim() || '/demo/goya_touch_packed.mp4',
} as const

export function isForestTheme(themeId: number | null | undefined): boolean {
  return themeId === FOREST_THEME_ID
}
