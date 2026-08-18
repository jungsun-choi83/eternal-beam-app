/**
 * 자발적 아이들 이벤트 스케줄러 — 선택/타이밍 로직 테스트.
 *
 * 실행: npm test  (node:test + node:assert)
 *
 * 가장 중요한 계약: **COME_CLOSER 는 절대 자발적으로 선택되지 않는다.**
 * 사용자가 부르지 않았는데 펫이 혼자 다가오는 것은 단순 버그가 아니라
 * 제품이 망가진 것처럼 보인다. 그래서 여러 각도로 확인한다.
 *
 * 난수는 전부 주입한다 — 결정적으로 검증하기 위해서.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  DEFAULT_SPONTANEOUS_WEIGHT,
  IDLE_COOLDOWN_MAX_MS,
  IDLE_COOLDOWN_MIN_MS,
  IDLE_QUIET_MAX_MS,
  IDLE_QUIET_MIN_MS,
  IDLE_TRIGGER_VERIFY_MS,
  eligibleIdleEvents,
  nextCooldownDelayMs,
  nextQuietDelayMs,
  randomDelayMs,
  selectIdleEvent,
  weightOf,
} from './idle-event-scheduler.ts'
import {
  RUNTIME_EVENTS,
  SEAM_WAIT_MAX_MS,
  registeredIdleEvents,
  type RuntimeEventDef,
} from './pet-runtime-events.ts'

const BLINK = 'BLINKING'
const EAR = 'EAR_TWITCHING'
const CC = 'COME_CLOSER'
const TILT = 'HEAD_TILTING'
const WAG = 'TAIL_WAGGING'
/** Phase 4 — 등록된 아이들 이벤트 전부. */
const ALL = [BLINK, EAR, TILT, WAG] as const
/** 두 개만 있던 시절의 조합 — 부분 가용성 검증에 계속 쓴다. */
const BOTH = [BLINK, EAR] as const

/** 고정 시퀀스 난수 — 결정적 검증용. */
const seq = (...values: number[]) => {
  let i = 0
  return () => values[Math.min(i++, values.length - 1)]
}

// ── 1) 후보는 등록된 아이들 이벤트에서만 나온다 ──────────────────────────────

test('1) 후보는 registeredIdleEvents() 에서만 나온다', () => {
  const ids = eligibleIdleEvents(ALL).map((d) => d.id)
  const registered = registeredIdleEvents().map((d) => d.id)
  for (const id of ids) assert.ok(registered.includes(id), `${id} 가 등록 목록 밖이다`)
  assert.deepEqual(ids.sort(), [...ALL].sort())
})

test('1b) 후보는 전부 kind=IDLE_EVENT 다', () => {
  for (const def of eligibleIdleEvents(ALL)) {
    assert.equal(def.kind, 'IDLE_EVENT', `${def.id} 가 아이들 이벤트가 아니다`)
  }
})

// ── 2) COME_CLOSER 는 절대 선택되지 않는다 ───────────────────────────────────

test('2) COME_CLOSER 는 소스를 줘도 후보가 되지 않는다', () => {
  const ids = eligibleIdleEvents([CC, ...ALL]).map((d) => d.id)
  assert.ok(!ids.includes(CC), '액션이 자발적 후보에 들어갔다')
  assert.deepEqual(ids.sort(), [...ALL].sort())
})

test('2b) COME_CLOSER 만 "사용 가능"해도 아무것도 선택하지 않는다', () => {
  const candidates = eligibleIdleEvents([CC])
  assert.deepEqual(candidates, [])
  assert.equal(selectIdleEvent(candidates, null, seq(0.5)), null)
})

test('2c) 난수를 전 구간 훑어도 COME_CLOSER 는 한 번도 나오지 않는다', () => {
  const candidates = eligibleIdleEvents([CC, ...ALL])
  for (let i = 0; i <= 100; i++) {
    const picked = selectIdleEvent(candidates, null, seq(i / 100))
    assert.notEqual(picked, CC, `r=${i / 100} 에서 COME_CLOSER 가 선택됐다`)
  }
})

// ── 3) BREATHING 에서 두 이벤트 모두 선택될 수 있다 ──────────────────────────

test('3) 등록된 아이들 이벤트 4종이 모두 선택될 수 있다', () => {
  const candidates = eligibleIdleEvents(ALL)
  const seen = new Set<string>()
  for (let i = 0; i < 200; i++) {
    const picked = selectIdleEvent(candidates, null, seq(i / 200))
    if (picked) seen.add(picked)
  }
  assert.deepEqual([...seen].sort(), [...ALL].sort())
})

test('3b) BLINKING 이 가장 흔하고, 나머지 셋은 서로 비슷하다', () => {
  const candidates = eligibleIdleEvents(ALL)
  const counts: Record<string, number> = {}
  const N = 2000
  for (let i = 0; i < N; i++) {
    const picked = selectIdleEvent(candidates, null, seq(i / N))
    if (picked) counts[picked] = (counts[picked] ?? 0) + 1
  }
  for (const id of [EAR, TILT, WAG]) {
    assert.ok(counts[BLINK] > counts[id], `BLINKING 이 ${id} 보다 많아야 한다`)
  }
  // 가중치 3:1:1:1 → BLINKING 이 약 절반.
  const ratio = counts[BLINK] / N
  assert.ok(ratio > 0.4 && ratio < 0.6, `BLINKING 비율이 가중치와 안 맞는다: ${ratio.toFixed(2)}`)
})

test('3c) 가중치는 레지스트리에서 온다 — 스케줄러에 하드코딩되지 않았다', () => {
  assert.equal(weightOf(RUNTIME_EVENTS[BLINK] as RuntimeEventDef), 3)
  assert.equal(weightOf(RUNTIME_EVENTS[EAR] as RuntimeEventDef), 1)
  // 가중치를 지정하지 않으면 기본값.
  assert.equal(
    weightOf({ ...(RUNTIME_EVENTS[BLINK] as RuntimeEventDef), spontaneousWeight: undefined }),
    DEFAULT_SPONTANEOUS_WEIGHT
  )
})

// ── 5) 연속 반복 회피 ────────────────────────────────────────────────────────

test('5) 대안이 있으면 같은 이벤트를 연속으로 고르지 않는다', () => {
  const candidates = eligibleIdleEvents(ALL)
  for (const last of ALL) {
    for (let i = 0; i <= 100; i++) {
      const r = i / 100
      assert.notEqual(selectIdleEvent(candidates, last, seq(r)), last, `last=${last} r=${r}`)
    }
  }
})

test('5c) 4종이 되면서 강제 교대가 풀렸다 — 직전 이벤트 뒤에 세 갈래가 열린다', () => {
  // 둘뿐일 때는 반복 회피가 곧 blink→ear→blink… 강제 교대였다.
  const candidates = eligibleIdleEvents(ALL)
  const after = new Set<string>()
  for (let i = 0; i <= 200; i++) {
    const picked = selectIdleEvent(candidates, BLINK, seq(i / 200))
    if (picked) after.add(picked)
  }
  assert.deepEqual([...after].sort(), [EAR, TILT, WAG].sort())
})

test('5b) 후보가 하나뿐이면 반복을 허용한다 (아니면 영영 재생되지 않는다)', () => {
  const only = eligibleIdleEvents([BLINK])
  assert.equal(selectIdleEvent(only, BLINK, seq(0.5)), BLINK)
})

// ── 6) 자산 누락은 안전하게 건너뛴다 ─────────────────────────────────────────

test('6) 소스가 없는 이벤트는 후보에서 빠진다', () => {
  assert.deepEqual(eligibleIdleEvents([BLINK]).map((d) => d.id), [BLINK])
  assert.deepEqual(eligibleIdleEvents([EAR]).map((d) => d.id), [EAR])
})

test('6b) 한쪽 자산만 있어도 그쪽은 정상 동작한다', () => {
  const onlyEar = eligibleIdleEvents([EAR])
  assert.equal(selectIdleEvent(onlyEar, null, seq(0.99)), EAR)
  assert.equal(selectIdleEvent(onlyEar, EAR, seq(0.01)), EAR)
})

test('6c) 자산이 하나도 없으면 null — 오류가 아니라 BREATHING 유지', () => {
  assert.deepEqual(eligibleIdleEvents([]), [])
  assert.equal(selectIdleEvent([], null, seq(0.5)), null)
  assert.equal(selectIdleEvent([], BLINK, seq(0.5)), null)
})

test('6d) 미선언 id 는 소스를 줘도 후보가 되지 않는다', () => {
  // Phase 4 로 선언된 4종이 전부 등록됐으므로, 이제 "미등록" 예시는 도메인 밖
  // 문자열로 든다 — 오타나 아직 없는 미래 이벤트가 스케줄러에 새지 않는지.
  const ids = eligibleIdleEvents(['WINKING', 'STRETCHING', 'YAWNING'] as never).map((d) => d.id)
  assert.deepEqual(ids, [])
})

test('6e) 가중치 0 이면 자발적 선택에서 빠진다 (수동 트리거는 그대로)', () => {
  const zeroed = { ...(RUNTIME_EVENTS[EAR] as RuntimeEventDef), spontaneousWeight: 0 }
  assert.equal(weightOf(zeroed), 0)
})

// ── 타이밍 ───────────────────────────────────────────────────────────────────

test('타이밍 상수는 튜닝 가능하고 유한하다', () => {
  assert.ok(IDLE_QUIET_MIN_MS > 0 && IDLE_QUIET_MIN_MS < IDLE_QUIET_MAX_MS)
  assert.ok(IDLE_COOLDOWN_MIN_MS > 0 && IDLE_COOLDOWN_MIN_MS < IDLE_COOLDOWN_MAX_MS)
  // 검증 유예는 이음매 대기 상한보다 길어야 한다 — 아니면 정상 재생을
  // "거절됐다"로 오판해 스케줄러가 이중으로 예약한다.
  assert.ok(
    IDLE_TRIGGER_VERIFY_MS > SEAM_WAIT_MAX_MS,
    `검증 유예(${IDLE_TRIGGER_VERIFY_MS})가 이음매 상한(${SEAM_WAIT_MAX_MS})보다 짧다`
  )
})

test('지연은 항상 [min, max] 안이고 랜덤하다 (고정 루프가 아니다)', () => {
  for (const r of [0, 0.25, 0.5, 0.75, 1]) {
    const q = nextQuietDelayMs(seq(r))
    assert.ok(q >= IDLE_QUIET_MIN_MS && q <= IDLE_QUIET_MAX_MS, `quiet=${q}`)
    const c = nextCooldownDelayMs(seq(r))
    assert.ok(c >= IDLE_COOLDOWN_MIN_MS && c <= IDLE_COOLDOWN_MAX_MS, `cooldown=${c}`)
  }
  assert.notEqual(nextQuietDelayMs(seq(0)), nextQuietDelayMs(seq(1)), '항상 같은 값이면 랜덤이 아니다')
})

test('지연 계산은 잘못된 설정에도 무너지지 않는다', () => {
  assert.equal(randomDelayMs(1000, 500, seq(0.5)), 1000, 'max<min 이면 min')
  assert.equal(randomDelayMs(-1, 100, seq(0.5)), 0)
  assert.equal(randomDelayMs(500, 500, seq(0.5)), 500)
  assert.ok(Number.isFinite(randomDelayMs(100, 200, () => Number.NaN)))
})
