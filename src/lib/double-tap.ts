/**
 * 더블탭/더블클릭 인식기 — 드래그·핀치와 공존해야 하므로 순수 함수로 뺐다.
 *
 * 왜 별도 리스너(onDoubleClick)를 쓰지 않는가:
 * 미리보기 표면은 이미 포인터 드래그(위치)와 핀치(크기)를 처리한다. 거기에
 * 더블클릭 리스너를 얹으면 두 경로가 같은 제스처를 두고 경쟁한다. 그래서
 * **기존 pointerup 안에서** 판정한다 — 드래그가 우선이고, 움직임이 거의 없을
 * 때만 탭으로 본다.
 *
 * 마우스와 터치를 모두 지원한다:
 *   마우스 : pointerdown/up 이 같은 pointerId(보통 1)로 두 번 — 그대로 통과
 *   터치   : 손가락마다 pointerId 가 달라지므로 id 를 비교하지 않는다
 */

export type TapPoint = { t: number; x: number; y: number };

export type DoubleTapConfig = {
  /** 이 거리 이하로 움직였으면 드래그가 아니라 탭. */
  moveTolerancePx: number;
  /** 두 탭 사이 최대 간격(ms). */
  maxGapMs: number;
  /** 두 탭이 서로 이만큼 안쪽이어야 같은 지점으로 인정. */
  maxDistancePx: number;
};

export const DEFAULT_DOUBLE_TAP: DoubleTapConfig = {
  moveTolerancePx: 8,
  maxGapMs: 300,
  maxDistancePx: 32,
};

export type TapResult =
  | { kind: "drag" }          // 움직임이 커서 탭이 아니다
  | { kind: "first"; tap: TapPoint }  // 첫 번째 탭 — 기억해 둔다
  | { kind: "double" };       // 더블탭 성립

/**
 * 포인터가 떨어졌을 때 한 번 호출한다.
 *
 * @param start   pointerdown 시점 좌표 (없으면 드래그 판정 불가 → drag 취급)
 * @param end     pointerup 좌표 + 시각
 * @param last    직전에 인정된 탭 (없으면 null)
 */
export function recognizeTap(
  start: { x: number; y: number } | undefined,
  end: TapPoint,
  last: TapPoint | null,
  config: DoubleTapConfig = DEFAULT_DOUBLE_TAP,
): TapResult {
  if (!start) return { kind: "drag" };

  const moved = Math.hypot(end.x - start.x, end.y - start.y);
  if (moved > config.moveTolerancePx) return { kind: "drag" };

  if (last) {
    const gap = end.t - last.t;
    const dist = Math.hypot(end.x - last.x, end.y - last.y);
    if (gap >= 0 && gap <= config.maxGapMs && dist <= config.maxDistancePx) {
      return { kind: "double" };
    }
  }
  return { kind: "first", tap: end };
}
