/**
 * Toss 테마 결제 왕복 동안 복원할 최소 상태.
 *
 * 결제창은 새 문서로 이동하므로 React state 는 사라진다. 누끼 자체는 기존
 * pending-generation 저장소가 보관하고, 여기서는 사용자가 결제하려던 테마 key 와
 * 결제 확인 여부만 보관한다. 같은 탭의 결제 왕복에만 필요하므로 sessionStorage 다.
 */

export const THEME_PURCHASE_RETURN_KEY = "eternal_beam_theme_purchase_return_v1";

export interface ThemePurchaseReturnState {
  themeKey: string;
  confirmed: boolean;
}

export function saveThemePurchaseReturnState(
  themeKey: string,
  confirmed = false
): void {
  const key = String(themeKey || "").trim();
  if (!key) return;
  try {
    sessionStorage.setItem(
      THEME_PURCHASE_RETURN_KEY,
      JSON.stringify({ themeKey: key, confirmed } satisfies ThemePurchaseReturnState)
    );
  } catch {
    /* 결제를 막지는 않는다. 복귀 시 상태 복원만 불가능해진다. */
  }
}

export function readThemePurchaseReturnState(): ThemePurchaseReturnState | null {
  try {
    const raw = sessionStorage.getItem(THEME_PURCHASE_RETURN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<ThemePurchaseReturnState>;
    const themeKey = String(parsed.themeKey || "").trim();
    if (!themeKey) return null;
    return { themeKey, confirmed: parsed.confirmed === true };
  } catch {
    return null;
  }
}

export function confirmThemePurchaseReturn(themeKey: string): void {
  saveThemePurchaseReturnState(themeKey, true);
}

export function clearThemePurchaseReturnState(): void {
  try {
    sessionStorage.removeItem(THEME_PURCHASE_RETURN_KEY);
  } catch {
    /* ignore */
  }
}
