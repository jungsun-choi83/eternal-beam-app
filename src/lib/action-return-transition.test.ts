/**
 * COME_CLOSER → BREATH 복귀 전환 타이밍 테스트.
 *
 * 실행: npm test  (node:test + node:assert)
 *
 * 배경: 예전 복귀는 활성 소스 포인터만 한 프레임에 바꿔치기해서, 클로즈업으로 끝난
 * 액션이 전신 BREATH 로 순간이동하는 것처럼 보였다. 여기서 고정하는 것은
 * "hold 동안 BREATH 가 절대 보이지 않는다"와 "디졸브가 반드시 배율 1 로 끝난다"
 * 두 가지다 — 둘 중 하나라도 깨지면 다시 컷처럼 보인다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  ACTION_EXIT_SCALE_GAIN,
  ARRIVAL_HOLD_MS,
  RETURN_CROSSFADE_MS,
  RETURN_START_SCALE_FALLBACK,
  RETURN_START_SCALE_MAX,
  RETURN_START_SCALE_MIN,
  RETURN_TOTAL_MS,
  clampReturnStartScale,
  computeReturnFrame,
  easeOutCubic,
} from './action-return-transition.ts'

const SCALE = 1.4

test('합의된 타이밍 — hold 300ms + 디졸브 600ms = 총 900ms', () => {
  assert.equal(ARRIVAL_HOLD_MS, 300)
  assert.equal(RETURN_CROSSFADE_MS, 600)
  assert.equal(RETURN_TOTAL_MS, 900)
})

test('hold 동안 BREATH 는 완전히 보이지 않고 액션은 그대로 정지해 있다', () => {
  for (const t of [0, 1, 150, 299.9]) {
    const f = computeReturnFrame(t, SCALE)
    assert.equal(f.phase, 'hold', `t=${t}`)
    assert.equal(f.actionAlpha, 1, `t=${t}`)
    assert.equal(f.actionScale, 1, `t=${t}`)
    assert.equal(f.idleAlpha, 0, `t=${t}`)
  }
})

test('hold 경계에서 디졸브가 시작되고 BREATH 는 측정된 배율에서 등장한다', () => {
  const f = computeReturnFrame(ARRIVAL_HOLD_MS, SCALE)
  assert.equal(f.phase, 'crossfade')
  assert.equal(f.idleAlpha, 0)
  assert.equal(f.idleScale, SCALE, 'BREATH 는 액션이 끝난 크기에서 출발해야 한다')
  assert.equal(f.actionAlpha, 1)
})

test('디졸브가 끝나면 BREATH 는 정확히 원래 크기·완전 불투명이다', () => {
  const f = computeReturnFrame(RETURN_TOTAL_MS, SCALE)
  assert.equal(f.phase, 'done')
  assert.equal(f.idleScale, 1, '1 이 아니면 평소 BREATH 가 확대된 채 남는다')
  assert.equal(f.idleAlpha, 1)
  assert.equal(f.actionAlpha, 0)
})

test('디졸브 중 BREATH 배율은 단조 감소하고 절대 1 미만으로 내려가지 않는다', () => {
  let prev = Infinity
  for (let t = ARRIVAL_HOLD_MS; t <= RETURN_TOTAL_MS; t += 25) {
    const { idleScale } = computeReturnFrame(t, SCALE)
    assert.ok(idleScale <= prev + 1e-9, `t=${t} 에서 배율이 다시 커졌다`)
    assert.ok(idleScale >= 1 - 1e-9, `t=${t} 에서 1 미만으로 축소됐다: ${idleScale}`)
    prev = idleScale
  }
})

test('감속 이징 — 전반부에 거리의 절반 이상을 되돌린다', () => {
  const mid = computeReturnFrame(ARRIVAL_HOLD_MS + RETURN_CROSSFADE_MS / 2, SCALE)
  const travelled = (SCALE - mid.idleScale) / (SCALE - 1)
  assert.ok(travelled > 0.5, `감속이 아니다 (전반부 진행 ${travelled.toFixed(2)})`)
})

test('알파는 선형 교차 — 중간에 합이 1 이라 밀도가 꺼지지 않는다', () => {
  const mid = computeReturnFrame(ARRIVAL_HOLD_MS + RETURN_CROSSFADE_MS / 2, SCALE)
  assert.ok(Math.abs(mid.actionAlpha - 0.5) < 1e-9)
  assert.ok(Math.abs(mid.idleAlpha - 0.5) < 1e-9)
  assert.ok(Math.abs(mid.actionAlpha + mid.idleAlpha - 1) < 1e-9)
})

test('액션은 사라지는 동안에도 아주 조금 더 다가온다', () => {
  const a = computeReturnFrame(ARRIVAL_HOLD_MS + 1, SCALE).actionScale
  const b = computeReturnFrame(RETURN_TOTAL_MS - 1, SCALE).actionScale
  assert.ok(b > a, '액션 배율이 증가하지 않는다 — 멈춰서 사라지는 것처럼 보인다')
  assert.ok(b <= 1 + ACTION_EXIT_SCALE_GAIN + 1e-9, '잔여 모멘텀이 과하다')
})

test('전환이 끝난 뒤로는 계속 done 이다 (워치독 지연 등)', () => {
  assert.equal(computeReturnFrame(RETURN_TOTAL_MS * 10, SCALE).phase, 'done')
})

test('비정상 경과 시간은 도착 프레임 유지로 떨어진다', () => {
  for (const t of [Number.NaN, -1, -1000]) {
    const f = computeReturnFrame(t, SCALE)
    assert.equal(f.phase, 'hold', `t=${t}`)
    assert.equal(f.idleAlpha, 0, `t=${t}`)
  }
})

test('배율 클램프 — 측정 실패/이상값은 안전 범위로', () => {
  assert.equal(clampReturnStartScale(null), RETURN_START_SCALE_FALLBACK)
  assert.equal(clampReturnStartScale(undefined), RETURN_START_SCALE_FALLBACK)
  assert.equal(clampReturnStartScale(Number.NaN), RETURN_START_SCALE_FALLBACK)
  // 0.6 = BREATH 가 액션보다 크다는 뜻 → 축소 등장을 막는다
  assert.equal(clampReturnStartScale(0.6), RETURN_START_SCALE_MIN)
  assert.equal(clampReturnStartScale(99), RETURN_START_SCALE_MAX)
  assert.equal(clampReturnStartScale(1.32), 1.32)
})

test('클램프된 배율로도 전환은 항상 1 로 수렴한다', () => {
  for (const s of [1, 1.35, RETURN_START_SCALE_MAX, 99, Number.NaN]) {
    assert.equal(computeReturnFrame(RETURN_TOTAL_MS, s as number).idleScale, 1)
  }
})

test('easeOutCubic 은 0..1 로 고정되고 범위를 벗어난 입력을 클램프한다', () => {
  assert.equal(easeOutCubic(0), 0)
  assert.equal(easeOutCubic(1), 1)
  assert.equal(easeOutCubic(-5), 0)
  assert.equal(easeOutCubic(5), 1)
  assert.ok(easeOutCubic(0.5) > 0.5, '감속 곡선이어야 한다')
})
