/**
 * My Library 카드 세 점 메뉴 배치 — 순수 규칙 (DOM 없이 테스트 가능).
 *
 * 메뉴는 카드 밖(document.body 포털)에 fixed 로 뜨므로 카드의 overflow:hidden 에
 * 잘리지 않는다. 버튼 아래에 오른쪽 정렬이 기본이고, 아래 공간이 모자라면 위로
 * 뒤집는다.
 */

export type MenuPlacement = "below" | "above";

export interface MenuAnchorRect {
  top: number;
  bottom: number;
  left: number;
  right: number;
}

export interface PlaceCardMenuInput {
  anchor: MenuAnchorRect;
  menuWidth: number;
  menuHeight: number;
  viewportWidth: number;
  viewportHeight: number;
}

export const MENU_GAP_PX = 4;
export const MENU_VIEWPORT_MARGIN_PX = 8;

export function placeCardMenu({
  anchor,
  menuWidth,
  menuHeight,
  viewportWidth,
  viewportHeight,
}: PlaceCardMenuInput): { top: number; left: number; placement: MenuPlacement } {
  const need = menuHeight + MENU_GAP_PX;
  const spaceBelow = viewportHeight - MENU_VIEWPORT_MARGIN_PX - anchor.bottom;
  const spaceAbove = anchor.top - MENU_VIEWPORT_MARGIN_PX;

  let placement: MenuPlacement;
  if (spaceBelow >= need) placement = "below";
  else if (spaceAbove >= need) placement = "above";
  else placement = spaceBelow >= spaceAbove ? "below" : "above";

  const rawTop = placement === "below" ? anchor.bottom + MENU_GAP_PX : anchor.top - MENU_GAP_PX - menuHeight;
  const maxTop = viewportHeight - MENU_VIEWPORT_MARGIN_PX - menuHeight;
  const top = Math.max(MENU_VIEWPORT_MARGIN_PX, Math.min(rawTop, maxTop));

  const maxLeft = viewportWidth - MENU_VIEWPORT_MARGIN_PX - menuWidth;
  const left = Math.max(MENU_VIEWPORT_MARGIN_PX, Math.min(anchor.right - menuWidth, maxLeft));

  return { top, left, placement };
}
