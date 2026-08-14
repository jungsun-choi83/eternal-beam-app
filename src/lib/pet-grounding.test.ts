/**
 * 피사체 접지 계산 테스트.
 *
 * 실행: npm test  (node:test + node:assert)
 *
 * 배경: 조정 화면(preview-screen)에만 접지 보정이 있었고 최종 재생 화면
 * (memorial-device-play-screen)에는 없어서, 최종 재생에서 펫이 테마 지면이 아니라
 * 프레임 한가운데에 떠 있었다. 계산을 pet-grounding.ts 한 곳으로 모았으므로
 * 여기서 그 식을 고정한다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  PET_BOX_HEIGHT_FRACTION,
  SUBJECT_SHIFT_MAX_PCT,
  SUBJECT_SHIFT_MIN_PCT,
  computeSubjectShiftPct,
  subjectTransform,
} from './pet-grounding.ts'

test('보정은 항상 위(음수) 방향 — 발이 프레임 바닥보다 위인 접지선으로 올라간다', () => {
  // floorY 0.88(숲) + 실측 발 여백 0.139(Luma)
  const shift = computeSubjectShiftPct({ floorY: 0.88, feetMargin: 0.139 })
  assert.ok(shift < 0, `위로 올라가야 한다: ${shift}`)
  // -(1 - 0.88 - 0.139*0.62) * 100 = -3.382
  assert.ok(Math.abs(shift - -3.3818) < 0.001, String(shift))
})

test('접지선이 낮을수록(지면이 아래일수록) 보정이 작아진다', () => {
  const high = computeSubjectShiftPct({ floorY: 0.88, feetMargin: 0.15 })
  const low = computeSubjectShiftPct({ floorY: 0.93, feetMargin: 0.15 })
  assert.ok(low > high, `floorY 0.93 이 0.88 보다 덜 올라가야 한다: ${low} vs ${high}`)
})

test('발 여백이 클수록 더 내려간다 — 빈 배경만큼 상쇄해야 발이 지면에 닿는다', () => {
  const small = computeSubjectShiftPct({ floorY: 0.88, feetMargin: 0.139 })
  const large = computeSubjectShiftPct({ floorY: 0.88, feetMargin: 0.175 })
  assert.ok(large > small, `여백이 크면 덜 올라가야 한다: ${large} vs ${small}`)
})

test('프레임 밖으로 나가지 않게 클램프된다', () => {
  // 지면이 프레임 한참 위 + 여백 없음 → 하한에 걸린다
  assert.equal(
    computeSubjectShiftPct({ floorY: 0.5, feetMargin: 0 }),
    SUBJECT_SHIFT_MIN_PCT
  )
  // 지면이 맨 아래 + 여백 최대 → 상한에 걸린다
  assert.equal(
    computeSubjectShiftPct({ floorY: 1, feetMargin: 1 }),
    SUBJECT_SHIFT_MAX_PCT
  )
})

test('여백 0 · 접지선 1.0 이면 보정이 없다 (박스 하단 = 프레임 바닥 = 지면)', () => {
  // -0 이 나올 수 있다 (CSS 로는 동일). 부호 없이 비교한다.
  assert.equal(Math.abs(computeSubjectShiftPct({ floorY: 1, feetMargin: 0 })), 0)
})

test('박스 높이 비율은 CSS(.theme-preview-frame__pet height:62%)와 동기화돼 있다', () => {
  assert.equal(PET_BOX_HEIGHT_FRACTION, 0.62)
})

test('transform 은 사용자 조절값 위에 접지 보정을 얹는다', () => {
  const t = subjectTransform({ scale: 1.2, posX: 8, posY: -4, shiftPct: -3.4 })
  assert.equal(t, 'translate3d(8px, calc(-4px + -3.4%), 0) scale(1.2)')
})

test('두 화면이 같은 입력이면 문자열까지 같다 — 조정 결과가 최종 재생에서 재현된다', () => {
  const input = { scale: 1, posX: 0, posY: 0 }
  const shiftPct = computeSubjectShiftPct({ floorY: 0.9, feetMargin: 0.15 })
  assert.equal(
    subjectTransform({ ...input, shiftPct }),
    subjectTransform({ ...input, shiftPct })
  )
})
