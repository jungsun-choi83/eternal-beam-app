import type { ThemePurchaseReturnState } from "@/lib/theme-purchase-return-state";

/**
 * 결제 왕복 표식을 실제로 어떻게 적용할지에 대한 **순수 판단**.
 *
 * 부작용(React state 갱신·localStorage 기록·sessionStorage 삭제)은 호출부
 * (EternalBeamApp)가 한다 — 여기서는 "무엇을 해야 하는가"만 answer 한다. 슬롯
 * 복원(rehydrated vs fresh)은 이 함수 바깥, petSlots 초기화 시점에 이미 끝나
 * 있다는 전제다 — activePetSlotIndex 는 항상 유효하다.
 */
export interface ThemePurchaseReturnApplyResult {
  /** 적용할 테마 id. null 이면 아무 테마도 덮어쓰지 않는다. */
  themeId: number | null;
  /** 결제 왕복 표식(sessionStorage)을 지워도 되는가. */
  clearMarker: boolean;
}

const NO_OP: ThemePurchaseReturnApplyResult = { themeId: null, clearMarker: false };

export function resolveThemePurchaseReturnApply(
  state: ThemePurchaseReturnState | null,
  lookupThemeId: (themeKey: string) => number | undefined,
): ThemePurchaseReturnApplyResult {
  if (!state) return NO_OP;

  // 결제를 마치지 않고 돌아왔다 — 이번 시도는 끝났다. 표식만 치운다.
  if (!state.confirmed) {
    return { themeId: null, clearMarker: true };
  }

  const themeId = lookupThemeId(state.themeKey);
  // themeKey 를 모른다 — 카탈로그가 바뀌었거나 표식이 손상됐다. 확인된 결제를
  // 조용히 잃어버리는 것보다, 다음 로드에서도 다시 시도하도록 표식을 남긴다.
  if (themeId == null) return NO_OP;

  return { themeId, clearMarker: true };
}
