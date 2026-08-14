/**
 * 더블탭 인식기 — 마우스 더블클릭과 터치 더블탭 **둘 다** 성립해야 한다.
 *
 * "더블클릭했는데 아무 일도 안 난다" 는 신고를 코드 추론이 아니라 시퀀스
 * 시뮬레이션으로 확인하기 위한 테스트다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { DEFAULT_DOUBLE_TAP, recognizeTap, type TapPoint } from './double-tap.ts'

/** pointerup 들을 순서대로 흘려보내고 'double' 이 몇 번 났는지 센다. */
function playSequence(
  events: { start: { x: number; y: number }; end: TapPoint }[],
): { doubles: number; results: string[] } {
  let last: TapPoint | null = null
  let doubles = 0
  const results: string[] = []
  for (const e of events) {
    const r = recognizeTap(e.start, e.end, last)
    results.push(r.kind)
    if (r.kind === 'double') {
      doubles += 1
      last = null // 소비했으면 초기화 — 3연타가 2번 발동하면 안 된다
    } else if (r.kind === 'first') {
      last = r.tap
    }
  }
  return { doubles, results }
}

const at = (x: number, y: number, t: number) => ({ start: { x, y }, end: { x, y, t } })

// ── 마우스 더블클릭 ─────────────────────────────────────────────────────────

test('데스크톱 마우스 더블클릭 → 인식된다', () => {
  // 같은 좌표, 120ms 간격, 이동 0
  const { doubles, results } = playSequence([at(100, 100, 1000), at(100, 100, 1120)])
  assert.equal(doubles, 1)
  assert.deepEqual(results, ['first', 'double'])
})

test('마우스가 몇 px 흔들려도 인식된다 (손떨림 허용)', () => {
  const seq = [
    { start: { x: 100, y: 100 }, end: { x: 103, y: 102, t: 1000 } },
    { start: { x: 104, y: 101 }, end: { x: 106, y: 104, t: 1150 } },
  ]
  assert.equal(playSequence(seq).doubles, 1)
})

test('느린 두 번 클릭(>300ms)은 더블이 아니다', () => {
  const { doubles, results } = playSequence([at(100, 100, 1000), at(100, 100, 1500)])
  assert.equal(doubles, 0)
  assert.deepEqual(results, ['first', 'first'])
})

test('멀리 떨어진 두 클릭은 더블이 아니다', () => {
  assert.equal(playSequence([at(100, 100, 1000), at(300, 300, 1100)]).doubles, 0)
})

// ── 터치 더블탭 ─────────────────────────────────────────────────────────────

test('모바일 더블탭 → 인식된다 (pointerId 가 달라도 무관)', () => {
  assert.equal(playSequence([at(200, 400, 5000), at(202, 398, 5180)]).doubles, 1)
})

test('탭 세 번은 더블 한 번만 (연타 재진입 방지)', () => {
  const { doubles } = playSequence([at(100, 100, 1000), at(100, 100, 1100), at(100, 100, 1200)])
  assert.equal(doubles, 1, '3연타가 2번 발동하면 액션이 두 번 트리거된다')
})

// ── 드래그/핀치 보호 ────────────────────────────────────────────────────────

test('드래그는 탭으로 세지 않는다', () => {
  const drag = { start: { x: 100, y: 100 }, end: { x: 180, y: 140, t: 1000 } }
  assert.deepEqual(recognizeTap(drag.start, drag.end, null), { kind: 'drag' })
})

test('드래그 뒤 탭 한 번은 더블이 아니다', () => {
  const seq = [
    { start: { x: 100, y: 100 }, end: { x: 200, y: 100, t: 1000 } }, // 드래그
    at(200, 100, 1100),                                              // 탭
  ]
  const { doubles, results } = playSequence(seq)
  assert.equal(doubles, 0)
  assert.deepEqual(results, ['drag', 'first'])
})

test('탭 → 드래그 → 탭 은 더블이 아니다', () => {
  const seq = [
    at(100, 100, 1000),
    { start: { x: 100, y: 100 }, end: { x: 220, y: 100, t: 1100 } },
    at(220, 100, 1200),
  ]
  assert.equal(playSequence(seq).doubles, 0, '중간 드래그가 더블을 끊어야 한다')
})

test('start 가 없으면(포인터 추적 실패) 드래그로 안전하게 처리', () => {
  assert.deepEqual(recognizeTap(undefined, { x: 1, y: 1, t: 1 }, null), { kind: 'drag' })
})

// ── 경계값 ──────────────────────────────────────────────────────────────────

test('임계값 경계', () => {
  const c = DEFAULT_DOUBLE_TAP
  // 이동 허용 경계
  assert.equal(recognizeTap({ x: 0, y: 0 }, { x: c.moveTolerancePx, y: 0, t: 0 }, null).kind, 'first')
  assert.equal(
    recognizeTap({ x: 0, y: 0 }, { x: c.moveTolerancePx + 1, y: 0, t: 0 }, null).kind, 'drag')
  // 시간 경계
  const last = { x: 0, y: 0, t: 0 }
  assert.equal(recognizeTap({ x: 0, y: 0 }, { x: 0, y: 0, t: c.maxGapMs }, last).kind, 'double')
  assert.equal(recognizeTap({ x: 0, y: 0 }, { x: 0, y: 0, t: c.maxGapMs + 1 }, last).kind, 'first')
  // 거리 경계
  assert.equal(
    recognizeTap({ x: c.maxDistancePx, y: 0 }, { x: c.maxDistancePx, y: 0, t: 100 }, last).kind,
    'double')
})
