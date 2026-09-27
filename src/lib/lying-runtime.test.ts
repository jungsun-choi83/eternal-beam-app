/**
 * LYING 런타임 — 무활동 눕기 / 깨우기 후 액션 / 세트 미완성 방어 / 활동 리셋.
 *
 * 컨트롤러는 타이머·난수·발화가 전부 주입되므로 React 없이 결정론으로 돈다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  LIE_DOWN_INACTIVITY_MAX_MS,
  LIE_DOWN_INACTIVITY_MIN_MS,
  LyingRuntimeController,
  pickLieDownDelayMs,
  planUserAction,
  transitionSetReady,
} from './lying-runtime.ts'
import type { RuntimeEventId } from './pet-runtime-events.ts'

/** 수동 타이머 하네스 — advance() 로만 시간이 간다. */
function harness(random = () => 0) {
  const fired: RuntimeEventId[] = []
  const timers = new Map<number, { fn: () => void; at: number }>()
  let now = 0
  let seq = 0
  const c = new LyingRuntimeController({
    fire: (id) => fired.push(id),
    random,
    setTimer: (fn, ms) => {
      const h = ++seq
      timers.set(h, { fn, at: now + ms })
      return h
    },
    clearTimer: (h) => timers.delete(h as number),
  })
  const advance = (ms: number) => {
    now += ms
    for (const [h, t] of [...timers.entries()]) {
      if (t.at <= now) {
        timers.delete(h)
        t.fn()
      }
    }
  }
  return { c, fired, advance, pendingTimers: () => timers.size }
}

const READY = {
  LIE_DOWN: 'https://cdn.test/ld.mp4',
  LIE_IDLE: 'https://cdn.test/li.mp4',
  STAND_UP: 'https://cdn.test/su.mp4',
}

test('무활동 지연은 45~90s 균등 난수다', () => {
  assert.equal(pickLieDownDelayMs(() => 0), LIE_DOWN_INACTIVITY_MIN_MS)
  assert.equal(pickLieDownDelayMs(() => 1), LIE_DOWN_INACTIVITY_MAX_MS)
  const mid = pickLieDownDelayMs(() => 0.5)
  assert.ok(mid > LIE_DOWN_INACTIVITY_MIN_MS && mid < LIE_DOWN_INACTIVITY_MAX_MS)
})

test('무활동 → LIE_DOWN → (체인) LIE_IDLE 로 누움', () => {
  const { c, fired, advance } = harness(() => 0) // 45s 고정
  c.setTransitionReadiness(true)
  c.onEventChange(null) // 홈 도착

  advance(44_999)
  assert.deepEqual(fired, [], '45s 전에 눕지 않는다')
  advance(1)
  assert.deepEqual(fired, ['LIE_DOWN'])

  // 플레이어가 LIE_DOWN → 체인 → LIE_IDLE 를 알린다 (체인은 플레이어 계약 —
  // pet-runtime-events POSE-4 가 pin). 누운 동안 타이머는 다시 서지 않는다.
  c.onEventChange('LIE_DOWN')
  c.onEventChange('LIE_IDLE')
  advance(300_000)
  assert.deepEqual(fired, ['LIE_DOWN'], '누운 채 또 눕지 않는다')
})

test('LYING + COME_CLOSER → STAND_UP 먼저, 홈 도착 후 COME_CLOSER', () => {
  const { c, fired } = harness()
  c.setTransitionReadiness(true)
  c.onEventChange('LIE_IDLE') // 누운 홈

  c.dispatchUserAction('COME_CLOSER')
  assert.deepEqual(fired, ['STAND_UP'], '요청은 기억하고 먼저 일어난다')

  c.onEventChange('STAND_UP')      // 일어나는 중 — 아직 아무것도 더 쏘지 않는다
  assert.deepEqual(fired, ['STAND_UP'])
  c.onEventChange(null)            // STAND_UP 복귀 완료 = 홈 도착
  assert.deepEqual(fired, ['STAND_UP', 'COME_CLOSER'], '기억한 액션이 이어진다')
})

test('전이 자산이 하나라도 없으면 눕지 않는다 (HOME 유지)', () => {
  assert.equal(transitionSetReady(READY), true)
  assert.equal(transitionSetReady({ ...READY, STAND_UP: null }), false)
  assert.equal(transitionSetReady({ ...READY, LIE_IDLE: '' }), false)
  assert.equal(transitionSetReady({}), false)

  const { c, fired, advance, pendingTimers } = harness(() => 0)
  c.setTransitionReadiness(false) // 세트 미완성
  c.onEventChange(null)
  assert.equal(pendingTimers(), 0, '타이머 자체가 서지 않는다')
  advance(600_000)
  assert.deepEqual(fired, [], '어떤 발화도 없다 — HOME 유지')

  // 세트가 미완성이면 깨우기도 불가 — 요청은 조용히 무시된다 (decideTrigger 의
  // wrong-pose 거절과 같은 결과이며, STAND_UP 없이 펫을 세울 방법이 없다).
  c.onEventChange('LIE_IDLE')
  c.dispatchUserAction('COME_CLOSER')
  assert.deepEqual(fired, [])
})

test('사용자 활동이 무활동 타이머를 리셋한다', () => {
  const { c, fired, advance } = harness(() => 0) // 45s 고정
  c.setTransitionReadiness(true)
  c.onEventChange(null)

  advance(40_000)
  c.notifyActivity() // 44초 시점의 탭 — 타이머가 처음부터 다시
  advance(40_000)    // 총 80s 지났지만 리셋 후 40s
  assert.deepEqual(fired, [], '리셋 후 45s 가 다시 차야 한다')
  advance(5_000)
  assert.deepEqual(fired, ['LIE_DOWN'])
})

test('서 있는 동안의 요청은 그대로 발화되고 활동으로 친다', () => {
  const { c, fired, advance } = harness(() => 0)
  c.setTransitionReadiness(true)
  c.onEventChange(null)
  advance(44_000)
  c.dispatchUserAction('PET_HEAD') // direct + 활동 리셋
  assert.deepEqual(fired, ['PET_HEAD'])
  advance(2_000) // 원래 타이머였다면 여기서 눕는다
  assert.deepEqual(fired, ['PET_HEAD'], '직접 발화가 타이머를 리셋했다')
})

test('planUserAction — PET_HEAD 도 현재 규칙상 LYING 에선 wake-first 다', () => {
  assert.deepEqual(planUserAction('COME_CLOSER', 'LIE_IDLE', true), {
    type: 'wake-first', wake: 'STAND_UP',
  })
  // 현재 PET_HEAD 는 requiredPose 기본(STANDING) — 누운 자세 직접 재생을
  // 지원하지 않으므로 깨우기 경유가 맞다. 누운 쓰다듬기 클립이 생기면
  // 정의에 분기를 더한다.
  assert.deepEqual(planUserAction('PET_HEAD', 'LIE_IDLE', true), {
    type: 'wake-first', wake: 'STAND_UP',
  })
  assert.deepEqual(planUserAction('STAND_UP', 'LIE_IDLE', true), { type: 'direct' })
  assert.deepEqual(planUserAction('COME_CLOSER', null, true), { type: 'direct' })
  assert.deepEqual(planUserAction('COME_CLOSER', 'LIE_IDLE', false), {
    type: 'blocked', reason: 'cannot-wake',
  })
})

test('배선 — 플레이 화면이 컨트롤러 경유로 발화하고 이벤트 변경을 연결한다', () => {
  const src = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')
  assert.match(src, /useLyingRuntime/)
  assert.match(src, /lying\.dispatchUserAction\("COME_CLOSER"\)/)
  assert.match(src, /lying\.dispatchUserAction\("PET_HEAD"\)/)
  assert.match(src, /lying\.dispatchUserAction\(id\)/)     // 센서 경로
  assert.match(src, /lying\.notifyActivity\(\)/)            // pointerdown 리셋
  assert.match(src, /onRuntimeEventChange=\{lying\.onRuntimeEventChange\}/)
})
