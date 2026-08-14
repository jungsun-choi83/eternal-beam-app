/**
 * 피사체(펫) 접지 계산 — 조정 화면과 최종 재생 화면이 **같은 식**을 쓴다.
 *
 * 두 화면이 각자 배치를 계산하던 시절, 최종 재생 화면(memorial-device-play-screen)
 * 에는 이 보정이 아예 없었다(`items-center` + 보정 없는 transform). 그래서 펫이
 * 테마 지면이 아니라 프레임 한가운데에 떠 있었다. 계산은 이 파일 한 곳에만 둔다.
 *
 * React 를 import 하지 않는다 — `npm test`(node --test src/lib/*.test.ts)가 이
 * 파일을 그대로 로드할 수 있어야 하기 때문이다. 상태를 들고 있는 훅은
 * components/memorial/use-pet-grounding.ts 쪽이다.
 */

/**
 * 피사체 박스가 프레임 높이에서 차지하는 비율.
 * `.theme-preview-frame__pet { height: 62% }`(memorial-premium.css)와 **반드시**
 * 일치해야 한다. 한쪽만 바꾸면 발이 지면에서 어긋난다.
 */
export const PET_BOX_HEIGHT_FRACTION = 0.62;

/** 보정량 하한(위로) — 이보다 올리면 피사체가 프레임 위로 빠져나간다. */
export const SUBJECT_SHIFT_MIN_PCT = -40;
/** 보정량 상한(아래로). */
export const SUBJECT_SHIFT_MAX_PCT = 10;

export interface SubjectShiftInput {
  /** 테마 접지선 (0=맨 위, 1=맨 아래). themes.getThemeFloorY() 결과. */
  floorY: number;
  /** 클립 하단의 빈 배경 비율 (0~1). IdleLoopVideo 가 클립당 1회 실측한다. */
  feetMargin: number;
  /** 기본값은 CSS 와 동기화된 PET_BOX_HEIGHT_FRACTION. */
  petBoxHeightFraction?: number;
}

/**
 * 피사체 박스를 세로로 얼마나 내릴지(프레임 높이 대비 %). 음수 = 위로.
 *
 * 박스는 `items-end` 로 이미 프레임 바닥에 붙어 있으므로, 기본 상태의 박스 하단은
 * 1.0(프레임 맨 아래)이다. 목표는 **박스 하단**이 아니라 **실제 발**이 floorY 에
 * 오는 것이고, 발은 박스 하단에서 `feetMargin × 박스높이`만큼 위에 있다.
 *   목표 박스 하단 = floorY + feetMargin × 박스높이
 *   이동량        = 목표 - 현재(1.0)
 *
 * %(퍼센트)를 쓰는 이유: translateY 의 % 는 요소 자신의 높이 기준인데, 이 레이어는
 * inset-0 이라 곧 프레임 높이다. padding-bottom 의 % 는 CSS 규격상 컨테이너
 * **너비** 기준이라 접지 계산에 쓸 수 없다.
 */
export function computeSubjectShiftPct({
  floorY,
  feetMargin,
  petBoxHeightFraction = PET_BOX_HEIGHT_FRACTION,
}: SubjectShiftInput): number {
  const raw = -(1 - floorY - feetMargin * petBoxHeightFraction) * 100;
  return Math.min(SUBJECT_SHIFT_MAX_PCT, Math.max(SUBJECT_SHIFT_MIN_PCT, raw));
}

export interface SubjectTransformInput {
  scale: number;
  posX: number;
  posY: number;
  /** computeSubjectShiftPct() 결과. */
  shiftPct: number;
}

/**
 * 피사체 레이어의 transform 문자열.
 *
 * 사용자 조절값(posX/posY/scale) 위에 접지 보정을 얹는다. 두 화면이 문자열까지
 * 동일해야 조정 화면에서 맞춘 위치가 최종 재생에서 그대로 재현된다.
 * `.preview-subject-layer { transform-origin: center bottom }` 이 전제다 —
 * center center 면 확대할 때 발이 같이 떠오른다.
 */
export function subjectTransform({
  scale,
  posX,
  posY,
  shiftPct,
}: SubjectTransformInput): string {
  return `translate3d(${posX}px, calc(${posY}px + ${shiftPct}%), 0) scale(${scale})`;
}
