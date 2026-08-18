/**
 * '건너뛰기(Skip)' 를 눌렀을 때 쓸 테마 id 결정.
 *
 * Skip 은 "새로 고르지 않겠다" 는 뜻이지 **"고른 걸 버리겠다" 가 아니다.**
 * 예전 handleThemeSkip 은 무조건 freeMemorialThemes[0](fresh_forest)로 덮어써서,
 * snow_forest 를 고른 뒤 Skip 을 누르면 선택이 사라졌다 — React state 뿐 아니라
 * localStorage 까지 덮여 새로고침해도 되돌아오지 않았다. 그 결과 미리보기와
 * COME_CLOSER 조회(place_id)까지 전부 엉뚱한 테마를 썼다.
 *
 * 테마 목록을 import 하지 않고 주입받는다 — `@/` 별칭 모듈은 node:test 에서
 * 해석되지 않기 때문이다(pet-ready-payload.ts / come-closer-asset.ts 와 같은 이유).
 */

export type ThemeSkipOptions = {
  /** 해당 id 가 실제 테마인지 확인. */
  isValidTheme: (id: number) => boolean;
  /** 아무것도 고르지 않았을 때 쓸 기본 무료 테마 id. */
  defaultThemeId: number;
};

export function resolveSkipThemeId(
  selectedTheme: number | null | undefined,
  { isValidTheme, defaultThemeId }: ThemeSkipOptions,
): number {
  if (selectedTheme != null && isValidTheme(selectedTheme)) {
    return selectedTheme; // 사용자의 명시적 선택을 유지한다
  }
  return defaultThemeId;
}
