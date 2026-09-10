/**
 * 펫 런타임 이벤트 도메인 — 분류 / 등록 / 트리거 정책 테스트.
 *
 * 실행: npm test  (node:test + node:assert)
 *
 * 지키려는 것 두 가지:
 *  1) **분류**. 아이들 이벤트(BLINKING 등)가 액션으로 취급되면, 앞으로의 자발적
 *     스케줄러가 COME_CLOSER 를 손으로 제외해야 하고 — 빠뜨리는 순간 펫이 혼자
 *     사용자에게 다가온다.
 *  2) **선언 ≠ 등록**. BLINKING 은 도메인에 있지만 아직 켜지지 않았다. 켜지지 않은
 *     것을 틀 수 있으면 자산 없는 <video> 가 마운트된다.
 *
 * Phase 0 계약(관측 동작)은 그대로여야 한다: 등록된 것은 COME_CLOSER 뿐이다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  sensorEventToRuntimeEvent,
  poseForCurrentEvent,
  IDLE_EVENT_IDS,
  IDLE_EVENT_PRIORITY,
  IDLE_HOME_STATE,
  PET_ACTION_IDS,
  RUNTIME_EVENTS,
  classifyRuntimeEvent,
  decideTrigger,
  getRuntimeEvent,
  isDeclaredRuntimeEvent,
  isIdleEvent,
  isPetAction,
  isRegisteredRuntimeEvent,
  mountableEvents,
  registeredActions,
  registeredIdleEvents,
  returnProfileFor,
  SEAM_EPSILON_S,
  SEAM_WAIT_MAX_MS,
  type RuntimeEventDef,
} from './pet-runtime-events.ts'

const CC = 'COME_CLOSER'
const PH = 'PET_HEAD'
const LU = 'LOOK_UP'
const LD = 'LIE_DOWN'
const SU = 'STAND_UP'
const LI = 'LIE_IDLE'
const BLINK = 'BLINKING'
const EAR = 'EAR_TWITCHING'
const TILT = 'HEAD_TILTING'
const WAG = 'TAIL_WAGGING'
/** Phase 4 기준 **등록되어 재생 가능한** 아이들 이벤트 — 이제 선언된 4종 전부다. */
const REGISTERED_IDLE = [BLINK, EAR, TILT, WAG] as const
/** 도메인에 선언된 아이들 이벤트 전부. */
const ALL_IDLE = [...REGISTERED_IDLE] as const

// ── 1) COME_CLOSER 는 ACTION 이다 ────────────────────────────────────────────

test('1) COME_CLOSER 는 ACTION 으로 분류된다', () => {
  assert.deepEqual(classifyRuntimeEvent(CC), { kind: 'ACTION', id: CC })
  assert.ok(isPetAction(CC))
  assert.ok(!isIdleEvent(CC), 'COME_CLOSER 가 아이들 이벤트로 새면 안 된다')
  assert.equal(RUNTIME_EVENTS[CC]?.kind, 'ACTION')
  assert.deepEqual([...PET_ACTION_IDS], [CC, PH, LU, LD, SU, LI])
})

// ── 2) 아이들 이벤트는 분류되지만 아직 등록되지 않았다 ────────────────────────

test('2) 아이들 이벤트 4종은 IDLE_EVENT 로 분류된다 (도메인 수준)', () => {
  for (const id of ALL_IDLE) {
    assert.deepEqual(classifyRuntimeEvent(id), { kind: 'IDLE_EVENT', id }, id)
    assert.ok(isIdleEvent(id), id)
    assert.ok(!isPetAction(id), `${id} 가 액션으로 분류됐다`)
    assert.ok(isDeclaredRuntimeEvent(id), `${id} 는 도메인에 선언돼 있어야 한다`)
  }
  assert.deepEqual([...IDLE_EVENT_IDS], [...ALL_IDLE])
})

test('8) 선언된 아이들 이벤트 4종이 모두 등록됐다 (Phase 4 완료)', () => {
  for (const id of ALL_IDLE) {
    assert.ok(RUNTIME_EVENTS[id], `${id} 가 등록돼 있지 않다`)
    assert.ok(isRegisteredRuntimeEvent(id), id)
    assert.equal(getRuntimeEvent(id)?.kind, 'IDLE_EVENT', id)
  }
  // 등록표는 정확히 액션 2종(COME_CLOSER + PET_HEAD) + 아이들 4종.
  assert.deepEqual(
    Object.keys(RUNTIME_EVENTS).sort(), [CC, PH, LU, LD, SU, LI, ...ALL_IDLE].sort())
})

test('8b) 미선언 id 는 여전히 트리거되지 않는다 (오타·미래 이벤트)', () => {
  for (const id of ['WINKING', 'STRETCHING', 'YAWNING']) {
    assert.ok(!isDeclaredRuntimeEvent(id), `${id} 가 선언돼 있다`)
    const d = decideTrigger({
      phase: 'IDLE', currentEventId: null, requestedEventId: id, hasSource: true,
    })
    assert.ok(!d.accepted, id)
    assert.equal(!d.accepted && d.reason, 'unknown-event', id)
  }
})

// ── 3) BREATHING 은 홈 상태이지 이벤트가 아니다 ──────────────────────────────

test('3) BREATHING 은 아이들 홈 상태이며 런타임 이벤트가 아니다', () => {
  assert.equal(IDLE_HOME_STATE, 'BREATHING')
  assert.ok(!isDeclaredRuntimeEvent('BREATHING'))
  assert.ok(!isPetAction('BREATHING'))
  assert.ok(!isIdleEvent('BREATHING'), 'BREATHING 은 아이들 "이벤트"가 아니라 바탕이다')
  assert.equal(classifyRuntimeEvent('BREATHING'), null)
  assert.equal(RUNTIME_EVENTS['BREATHING' as never], undefined)
})

test('3b) BREATHING 은 트리거 대상이 아니다 — 미선언으로 거절', () => {
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: 'BREATHING', hasSource: true,
  })
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'unknown-event')
})

// ── 4) COME_CLOSER 동작은 Phase 0 과 동일하다 ────────────────────────────────

test('4) COME_CLOSER 정의 — 최우선·중단 불가·테마 독립·부드러운 복귀·preload auto', () => {
  const def = RUNTIME_EVENTS[CC] as RuntimeEventDef
  assert.equal(def.interruptible, false, '한 번 시작되면 끊기지 않아야 한다')
  assert.equal(def.returnToIdle, true)
  assert.equal(def.entryPolicy, 'immediate', '사용자 조작은 기다리면 안 된다')
  assert.equal(def.returnPolicy, 'hold-and-dissolve', '기존 hold+디졸브 복귀를 유지해야 한다')
  assert.equal(def.themeIndependent, true)
  assert.equal(def.preload, 'auto', '더블탭 즉시 재생 — 기존 동작 그대로')
  assert.ok(def.priority > 0)
})

const ctx = (over: Partial<Parameters<typeof decideTrigger>[0]> = {}) => ({
  phase: 'IDLE' as const,
  currentEventId: null,
  requestedEventId: CC,
  hasSource: true,
  ...over,
})

test('4a) IDLE + 소스 있음 → 재생 수락 (더블탭 1회 = 1회 재생)', () => {
  const d = decideTrigger(ctx())
  assert.ok(d.accepted)
  assert.equal(d.accepted && d.event.id, CC)
})

test('4b) 재생 중 재트리거는 무시된다 — 중단 불가', () => {
  const d = decideTrigger(ctx({ phase: 'EVENT_PLAYING', currentEventId: CC }))
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'busy-non-interruptible')
})

test('4c) 복귀 전환 중에도 무시된다 — 전환은 끝까지 간다', () => {
  const d = decideTrigger(ctx({ phase: 'EVENT_RETURNING', currentEventId: CC }))
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'returning')
})

test('4d) 소스가 없으면 수락하지 않는다 — 호출부의 기존 폴백이 그대로 돈다', () => {
  const d = decideTrigger(ctx({ hasSource: false }))
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'no-source')
})

test('4e) 재생 중인데 무엇이 재생 중인지 모르면 보수적으로 거절한다', () => {
  const d = decideTrigger(ctx({ phase: 'EVENT_PLAYING', currentEventId: null }))
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'busy-non-interruptible')
})

test('4f) 소스가 붙은 등록 이벤트만 <video> 를 갖는다', () => {
  assert.deepEqual(mountableEvents({}).map((e) => e.id), [])
  assert.deepEqual(mountableEvents({ COME_CLOSER: null }).map((e) => e.id), [])
  assert.deepEqual(mountableEvents({ COME_CLOSER: '   ' }).map((e) => e.id), [])
  assert.deepEqual(mountableEvents({ COME_CLOSER: 'https://cdn/cc.mp4' }).map((e) => e.id), [CC])
  // 등록된 아이들 이벤트는 소스가 있으면 마운트된다.
  assert.deepEqual(mountableEvents({ BLINKING: 'https://cdn/blink.mp4' }).map((e) => e.id), [BLINK])
  assert.deepEqual(mountableEvents({ EAR_TWITCHING: 'https://cdn/ear.mp4' }).map((e) => e.id), [EAR])
  assert.deepEqual(mountableEvents({ HEAD_TILTING: 'https://cdn/t.mp4' }).map((e) => e.id), [TILT])
  assert.deepEqual(mountableEvents({ TAIL_WAGGING: 'https://cdn/w.mp4' }).map((e) => e.id), [WAG])
  // 소스가 없으면 <video> 자체가 없다 — preload:none 이라도 빈 엘리먼트는 안 만든다.
  assert.deepEqual(mountableEvents({ BLINKING: null }).map((e) => e.id), [])
})

// ── 5) 레거시 ACTION_ORDER 와의 격리 ─────────────────────────────────────────

test('5) 레거시 IDLE / TOUCH / VOICE / NFC 는 런타임 이벤트가 될 수 없다', () => {
  // 4코인/NFC/device sync 계약이 그 넷에 묶여 있다 — 여기 섞이면 안 된다.
  for (const legacy of ['IDLE', 'TOUCH', 'VOICE', 'NFC']) {
    assert.ok(!isDeclaredRuntimeEvent(legacy), `${legacy} 가 런타임 이벤트로 선언됐다`)
    assert.ok(!isPetAction(legacy), legacy)
    assert.ok(!isIdleEvent(legacy), legacy)
    assert.equal(classifyRuntimeEvent(legacy), null, legacy)
    assert.equal(getRuntimeEvent(legacy), null, legacy)

    const d = decideTrigger({
      phase: 'IDLE', currentEventId: null, requestedEventId: legacy, hasSource: true,
    })
    assert.ok(!d.accepted, `${legacy} 가 재생 수락됐다`)
    assert.equal(!d.accepted && d.reason, 'unknown-event', legacy)
  }
})

// ── 6) 미래 스케줄러가 아이들 이벤트만 열거할 수 있다 ────────────────────────

test('6) registeredIdleEvents() 는 COME_CLOSER 를 절대 포함하지 않는다', () => {
  const idle = registeredIdleEvents()
  assert.ok(!idle.some((e) => e.id === CC), '스케줄러가 액션을 집어 갈 수 있다')
  assert.ok(idle.every((e) => e.kind === 'IDLE_EVENT'))
  // Phase 2 — BLINKING + EAR_TWITCHING 이 등록돼 있다.
  assert.deepEqual(idle.map((e) => e.id).sort(), [...REGISTERED_IDLE].sort())
  assert.deepEqual(
    registeredActions().map((e) => e.id).sort(), [CC, PH, LU, LD, SU, LI].sort())
})

test('6b) 아이들 이벤트는 실제로 등록돼 있어 주입 없이 열거된다', () => {
  assert.deepEqual(registeredIdleEvents().map((e) => e.id).sort(), [...REGISTERED_IDLE].sort())
  assert.deepEqual(
    registeredActions().map((e) => e.id).sort(),
    [CC, PH, LU, LD, SU, LI].sort(), '액션 목록이 오염됐다')
})

test('6c) 등록된 어떤 것도 COME_CLOSER 보다 우선순위가 높지 않다', () => {
  const top = (RUNTIME_EVENTS[CC] as RuntimeEventDef).priority
  for (const def of [...registeredActions(), ...registeredIdleEvents()]) {
    assert.ok(def.priority <= top, `${def.id} 가 COME_CLOSER 보다 높다`)
  }
})

// ── Phase 1A — BLINKING ──────────────────────────────────────────────────────

test('1A-1) BLINKING 은 IDLE_EVENT 로 등록됐다 (ACTION 이 아니다)', () => {
  const def = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
  assert.ok(def, 'BLINKING 이 등록돼 있지 않다')
  assert.equal(def.kind, 'IDLE_EVENT')
  assert.ok(isIdleEvent(BLINK))
  assert.ok(!isPetAction(BLINK), 'BLINKING 이 액션으로 새면 스케줄러가 오염된다')
  assert.ok(isRegisteredRuntimeEvent(BLINK))
  assert.deepEqual(classifyRuntimeEvent(BLINK), { kind: 'IDLE_EVENT', id: BLINK })
})

test('1A-2) BLINKING 전환 정책 — 이음매 진입 / 이음매 정렬 복귀 / preload 없음', () => {
  const def = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
  assert.equal(def.entryPolicy, 'wait-for-seam', '아이들 이벤트는 이음매를 기다릴 수 있다')
  assert.equal(def.returnPolicy, 'seam-aligned')
  assert.notEqual(def.returnPolicy, 'hold-and-dissolve', 'COME_CLOSER 처리를 쓰면 이중상이 생긴다')
  assert.equal(def.interruptible, true, '사용자 액션이 밀어낼 수 있어야 한다')
  assert.equal(def.returnToIdle, true)
  assert.equal(def.preload, 'none', '아이들 이벤트를 프리로드하면 디코더가 늘어난다')
})

test('1A-3) 우선순위 — BLINKING < COME_CLOSER', () => {
  const blink = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
  const cc = RUNTIME_EVENTS[CC] as RuntimeEventDef
  assert.ok(blink.priority < cc.priority, `${blink.priority} < ${cc.priority} 여야 한다`)
})

test('1A-4) IDLE 에서 BLINKING 트리거는 수락된다 (1회 재생)', () => {
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: BLINK, hasSource: true,
  })
  assert.ok(d.accepted)
  assert.equal(d.accepted && d.event.id, BLINK)
})

test('1A-5) COME_CLOSER 는 재생 중인 BLINKING 을 선점한다', () => {
  for (const phase of ['EVENT_PENDING_SEAM', 'EVENT_PLAYING'] as const) {
    const d = decideTrigger({
      phase, currentEventId: BLINK, requestedEventId: CC, hasSource: true,
    })
    assert.ok(d.accepted, `${phase} 에서 선점하지 못했다`)
    assert.equal(d.accepted && d.event.id, CC)
  }
})

test('1A-6) BLINKING 은 COME_CLOSER 를 절대 끊지 못한다', () => {
  for (const phase of ['EVENT_PLAYING', 'EVENT_PENDING_SEAM'] as const) {
    const d = decideTrigger({
      phase, currentEventId: CC, requestedEventId: BLINK, hasSource: true,
    })
    assert.ok(!d.accepted, `${phase} 에서 COME_CLOSER 가 끊겼다`)
    assert.equal(!d.accepted && d.reason, 'busy-non-interruptible')
  }
  // 복귀 전환 중에도 마찬가지.
  const r = decideTrigger({
    phase: 'EVENT_RETURNING', currentEventId: CC, requestedEventId: BLINK, hasSource: true,
  })
  assert.ok(!r.accepted)
  assert.equal(!r.accepted && r.reason, 'returning')
})

test('1A-7) BLINKING 이 BLINKING 을 재트리거해도 겹치지 않는다', () => {
  const d = decideTrigger({
    phase: 'EVENT_PLAYING', currentEventId: BLINK, requestedEventId: BLINK, hasSource: true,
  })
  assert.ok(!d.accepted, '같은 아이들 이벤트가 자기 자신을 밀어내면 안 된다')
  assert.equal(!d.accepted && d.reason, 'lower-priority')
})

test('1A-8) 자산이 없으면 BLINKING 은 재생되지 않는다', () => {
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: BLINK, hasSource: false,
  })
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'no-source')
})

test('1A-9) 이음매 폴백 상수는 명시적이고 유한하다', () => {
  assert.ok(SEAM_EPSILON_S > 0 && SEAM_EPSILON_S < 0.5, '이음매 판정 창이 비현실적이다')
  assert.ok(SEAM_WAIT_MAX_MS > 0 && SEAM_WAIT_MAX_MS <= 5000, '무한 대기는 이벤트를 삼킨다')
})

test('1A-10) 복귀 프로파일 — BLINKING 은 배율 브리지를 쓰지 않고 훨씬 짧다', () => {
  const blink = returnProfileFor('seam-aligned')
  const cc = returnProfileFor('hold-and-dissolve')
  assert.equal(blink.scaleBridge, false, '같은 프레이밍에 배율 브리지는 해롭다')
  assert.equal(blink.holdMs, 0, '붙잡아 둘 도착 순간이 없다')
  assert.ok(blink.crossfadeMs > 0 && blink.crossfadeMs <= 200, '긴 디졸브는 이중상을 만든다')
  assert.ok(blink.crossfadeMs < cc.crossfadeMs)
  assert.equal(cc.scaleBridge, true, 'COME_CLOSER 는 배율 브리지를 유지해야 한다')
  assert.equal(returnProfileFor('immediate').crossfadeMs, 0)
})

// ── Phase 2 — EAR_TWITCHING ──────────────────────────────────────────────────

test('P2-1) EAR_TWITCHING 은 IDLE_EVENT 로 등록됐다 (ACTION 이 아니다)', () => {
  const def = RUNTIME_EVENTS[EAR] as RuntimeEventDef
  assert.ok(def, 'EAR_TWITCHING 이 등록돼 있지 않다')
  assert.equal(def.kind, 'IDLE_EVENT')
  assert.ok(isIdleEvent(EAR))
  assert.ok(!isPetAction(EAR), 'EAR_TWITCHING 이 액션으로 새면 스케줄러가 오염된다')
  assert.ok(isRegisteredRuntimeEvent(EAR))
  assert.deepEqual(classifyRuntimeEvent(EAR), { kind: 'IDLE_EVENT', id: EAR })
  assert.equal(def.themeIndependent, true)
})

test('P2-2) EAR_TWITCHING 은 BLINKING 과 **정확히 같은** 전환 정책을 쓴다', () => {
  // 값을 개별로 확인하지 않고 통째로 비교한다 — 하나만 어긋나도 그 이벤트만
  // 이음매를 무시하고 튀는데, 눈으로는 원인을 찾기 어렵다.
  const blink = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
  const ear = RUNTIME_EVENTS[EAR] as RuntimeEventDef
  for (const key of [
    'kind', 'priority', 'interruptible', 'returnToIdle',
    'entryPolicy', 'returnPolicy', 'themeIndependent', 'preload',
  ] as const) {
    assert.equal(ear[key], blink[key], `${key} 가 BLINKING 과 다르다`)
  }
  // 정책 자체도 못 박는다 (둘 다 같이 잘못되는 경우 방어).
  assert.equal(ear.entryPolicy, 'wait-for-seam')
  assert.equal(ear.returnPolicy, 'seam-aligned')
  assert.equal(ear.preload, 'none')
  assert.equal(ear.interruptible, true)
})

test('P2-3) 우선순위 — EAR_TWITCHING < COME_CLOSER', () => {
  const ear = (RUNTIME_EVENTS[EAR] as RuntimeEventDef).priority
  const cc = (RUNTIME_EVENTS[CC] as RuntimeEventDef).priority
  assert.ok(ear < cc, `${ear} < ${cc} 여야 한다`)
})

test('P2-4) IDLE 에서 EAR_TWITCHING 트리거는 수락된다 (1회 재생)', () => {
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: EAR, hasSource: true,
  })
  assert.ok(d.accepted)
  assert.equal(d.accepted && d.event.id, EAR)
})

test('P2-5) COME_CLOSER 는 재생 중/대기 중인 EAR_TWITCHING 을 선점한다', () => {
  for (const phase of ['EVENT_PENDING_SEAM', 'EVENT_PLAYING'] as const) {
    const d = decideTrigger({
      phase, currentEventId: EAR, requestedEventId: CC, hasSource: true,
    })
    assert.ok(d.accepted, `${phase} 에서 선점하지 못했다`)
    assert.equal(d.accepted && d.event.id, CC)
  }
})

test('P2-6) EAR_TWITCHING 은 COME_CLOSER 를 절대 끊지 못한다', () => {
  for (const phase of ['EVENT_PLAYING', 'EVENT_PENDING_SEAM'] as const) {
    const d = decideTrigger({
      phase, currentEventId: CC, requestedEventId: EAR, hasSource: true,
    })
    assert.ok(!d.accepted, `${phase} 에서 COME_CLOSER 가 끊겼다`)
    assert.equal(!d.accepted && d.reason, 'busy-non-interruptible')
  }
  const r = decideTrigger({
    phase: 'EVENT_RETURNING', currentEventId: CC, requestedEventId: EAR, hasSource: true,
  })
  assert.ok(!r.accepted)
  assert.equal(!r.accepted && r.reason, 'returning')
})

test('P2-7) 아이들 이벤트끼리는 서로 선점하지 않는다 (같은 우선순위)', () => {
  // 먼저 시작한 쪽이 끝까지 간다 — 눈 깜빡임 도중 귀가 끼어들면 두 클립이
  // 겹쳐 보이고, 어느 쪽 휴지 자세로 복귀할지도 모호해진다.
  for (const [current, requested] of [[BLINK, EAR], [EAR, BLINK], [EAR, EAR]] as const) {
    const d = decideTrigger({
      phase: 'EVENT_PLAYING', currentEventId: current, requestedEventId: requested, hasSource: true,
    })
    assert.ok(!d.accepted, `${current} 중 ${requested} 가 끼어들었다`)
    assert.equal(!d.accepted && d.reason, 'lower-priority')
  }
})

test('P2-8) 자산이 없으면 EAR_TWITCHING 은 재생되지 않는다', () => {
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: EAR, hasSource: false,
  })
  assert.ok(!d.accepted)
  assert.equal(!d.accepted && d.reason, 'no-source')
})

test('P2-9) BLINKING 은 Phase 2 로 인해 바뀌지 않았다', () => {
  const def = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
  assert.equal(def.kind, 'IDLE_EVENT')
  assert.equal(def.entryPolicy, 'wait-for-seam')
  assert.equal(def.returnPolicy, 'seam-aligned')
  assert.equal(def.interruptible, true)
  assert.equal(def.preload, 'none')
  assert.equal(def.themeIndependent, true)
  const d = decideTrigger({
    phase: 'IDLE', currentEventId: null, requestedEventId: BLINK, hasSource: true,
  })
  assert.ok(d.accepted)
})

test('P2-10) COME_CLOSER 는 Phase 2 로 인해 바뀌지 않았다', () => {
  const def = RUNTIME_EVENTS[CC] as RuntimeEventDef
  assert.equal(def.kind, 'ACTION')
  assert.equal(def.entryPolicy, 'immediate')
  assert.equal(def.returnPolicy, 'hold-and-dissolve')
  assert.equal(def.interruptible, false)
  assert.equal(def.preload, 'auto')
  assert.deepEqual(
    registeredActions().map((e) => e.id).sort(), [CC, PH, LU, LD, SU, LI].sort())
})

test('P2-11) 모든 아이들 이벤트가 같은 우선순위를 공유한다', () => {
  const priorities = new Set(registeredIdleEvents().map((e) => e.priority))
  assert.equal(priorities.size, 1, `아이들 이벤트 우선순위가 갈렸다: ${[...priorities]}`)
  assert.equal([...priorities][0], IDLE_EVENT_PRIORITY)
})

// ── Phase 4 — HEAD_TILTING / TAIL_WAGGING ────────────────────────────────────

for (const id of ['HEAD_TILTING', 'TAIL_WAGGING'] as const) {
  test(`P4) ${id} 은 IDLE_EVENT 로 등록됐다 (ACTION 이 아니다)`, () => {
    const def = RUNTIME_EVENTS[id] as RuntimeEventDef
    assert.ok(def, `${id} 이 등록돼 있지 않다`)
    assert.equal(def.kind, 'IDLE_EVENT')
    assert.ok(isIdleEvent(id))
    assert.ok(!isPetAction(id), `${id} 이 액션으로 새면 스케줄러가 오염된다`)
    assert.equal(def.themeIndependent, true)
  })

  test(`P4) ${id} 은 기존 아이들 이벤트와 **정확히 같은** 정책을 쓴다`, () => {
    const blink = RUNTIME_EVENTS[BLINK] as RuntimeEventDef
    const def = RUNTIME_EVENTS[id] as RuntimeEventDef
    for (const key of [
      'kind', 'priority', 'interruptible', 'returnToIdle',
      'entryPolicy', 'returnPolicy', 'themeIndependent', 'preload',
    ] as const) {
      assert.equal(def[key], blink[key], `${key} 가 BLINKING 과 다르다`)
    }
  })

  test(`P4) ${id} 은 BREATHING 에서 트리거되고 COME_CLOSER 에 밀린다`, () => {
    // 재생 수락
    const ok = decideTrigger({
      phase: 'IDLE', currentEventId: null, requestedEventId: id, hasSource: true,
    })
    assert.ok(ok.accepted)
    assert.equal(ok.accepted && ok.event.id, id)

    // COME_CLOSER 가 선점 (재생 중 / 이음매 대기 중 모두)
    for (const phase of ['EVENT_PLAYING', 'EVENT_PENDING_SEAM'] as const) {
      const pre = decideTrigger({
        phase, currentEventId: id, requestedEventId: CC, hasSource: true,
      })
      assert.ok(pre.accepted, `${phase} 에서 COME_CLOSER 가 선점하지 못했다`)
    }

    // 반대로 COME_CLOSER 는 절대 끊지 못한다
    for (const phase of ['EVENT_PLAYING', 'EVENT_PENDING_SEAM'] as const) {
      const no = decideTrigger({
        phase, currentEventId: CC, requestedEventId: id, hasSource: true,
      })
      assert.ok(!no.accepted, `${phase} 에서 COME_CLOSER 가 끊겼다`)
      assert.equal(!no.accepted && no.reason, 'busy-non-interruptible')
    }

    // 자산이 없으면 재생하지 않는다
    const noSrc = decideTrigger({
      phase: 'IDLE', currentEventId: null, requestedEventId: id, hasSource: false,
    })
    assert.ok(!noSrc.accepted)
    assert.equal(!noSrc.accepted && noSrc.reason, 'no-source')
  })
}

test('P4) 아이들 이벤트끼리는 여전히 서로 선점하지 않는다', () => {
  for (const current of REGISTERED_IDLE) {
    for (const requested of REGISTERED_IDLE) {
      const d = decideTrigger({
        phase: 'EVENT_PLAYING', currentEventId: current, requestedEventId: requested, hasSource: true,
      })
      assert.ok(!d.accepted, `${current} 중 ${requested} 가 끼어들었다`)
      assert.equal(!d.accepted && d.reason, 'lower-priority')
    }
  }
})

test('P4) 네 이벤트 모두 같은 우선순위이며 COME_CLOSER 보다 낮다', () => {
  const cc = (RUNTIME_EVENTS[CC] as RuntimeEventDef).priority
  const priorities = new Set(registeredIdleEvents().map((e) => e.priority))
  assert.equal(priorities.size, 1, `우선순위가 갈렸다: ${[...priorities]}`)
  assert.equal([...priorities][0], IDLE_EVENT_PRIORITY)
  assert.ok(IDLE_EVENT_PRIORITY < cc)
})

test('P4) COME_CLOSER 는 Phase 4 로 인해 바뀌지 않았다', () => {
  const def = RUNTIME_EVENTS[CC] as RuntimeEventDef
  assert.equal(def.kind, 'ACTION')
  assert.equal(def.entryPolicy, 'immediate')
  assert.equal(def.returnPolicy, 'hold-and-dissolve')
  assert.equal(def.interruptible, false)
  assert.equal(def.preload, 'auto')
  assert.deepEqual(
    registeredActions().map((e) => e.id).sort(), [CC, PH, LU, LD, SU, LI].sort())
})


// ── PET_HEAD (2026-09-08) — 첫 INTERACTION 액션, TOUCH/싱글탭 트리거 ──────────

test('PH-1) PET_HEAD 는 ACTION 으로 분류되고 등록돼 있다', () => {
  assert.deepEqual(classifyRuntimeEvent(PH), { kind: 'ACTION', id: PH })
  assert.ok(isPetAction(PH))
  assert.ok(isRegisteredRuntimeEvent(PH))
  const def = RUNTIME_EVENTS[PH] as RuntimeEventDef
  assert.equal(def.kind, 'ACTION')
  assert.equal(def.entryPolicy, 'immediate')   // 사용자가 유발 — 기다리지 않는다
  assert.equal(def.returnPolicy, 'seam-aligned') // 시작 포즈 복귀 — 디졸브 불필요
  assert.equal(def.interruptible, false)
  assert.equal(def.preload, 'none')            // 프리로드 예외는 COME_CLOSER 뿐
  assert.ok(def.priority < (RUNTIME_EVENTS[CC] as RuntimeEventDef).priority,
    '다가오기(100)보다 낮아야 한다')
  assert.ok(def.priority > IDLE_EVENT_PRIORITY, '아이들 이벤트보다는 높아야 한다')
})

test('PH-2) 낮은 우선순위 PET_HEAD 는 COME_CLOSER 재생을 밀어내지 못한다', () => {
  const d = decideTrigger(ctx({
    phase: 'EVENT_PLAYING', currentEventId: CC, requestedEventId: PH, hasSource: true,
  }))
  assert.deepEqual(d, { accepted: false, reason: 'busy-non-interruptible' })
})

test('PH-3) PET_HEAD 재생 중 COME_CLOSER 도 끼어들 수 없다 (non-interruptible)', () => {
  const d = decideTrigger(ctx({
    phase: 'EVENT_PLAYING', currentEventId: PH, requestedEventId: CC, hasSource: true,
  }))
  assert.deepEqual(d, { accepted: false, reason: 'busy-non-interruptible' })
})

test('PH-4) 소스가 없으면 no-source — OFF/미소유는 조용히 거절된다', () => {
  const d = decideTrigger(ctx({ requestedEventId: PH, hasSource: false }))
  assert.deepEqual(d, { accepted: false, reason: 'no-source' })
})

test('PH-5) 자발적 스케줄러 후보에 절대 들어가지 않는다', () => {
  assert.ok(registeredIdleEvents().every((e) => e.id !== PH))
})


// ── LOOK_UP (2026-09-08) — VOICE 트리거 액션 ─────────────────────────────────

test('LU-1) LOOK_UP 은 ACTION 으로 분류·등록되고 우선순위 계단이 맞다', () => {
  assert.deepEqual(classifyRuntimeEvent(LU), { kind: 'ACTION', id: LU })
  assert.ok(isRegisteredRuntimeEvent(LU))
  const def = RUNTIME_EVENTS[LU] as RuntimeEventDef
  assert.equal(def.kind, 'ACTION')
  assert.equal(def.entryPolicy, 'immediate')
  assert.equal(def.returnPolicy, 'seam-aligned')  // MICRO — 시작 포즈 복귀 → HOME
  assert.equal(def.returnToIdle, true)
  assert.equal(def.interruptible, false)
  assert.equal(def.preload, 'none')
  // 목소리(80) < 터치(90) < 다가오기(100) > 아이들(10)
  const ph = RUNTIME_EVENTS[PH] as RuntimeEventDef
  const cc = RUNTIME_EVENTS[CC] as RuntimeEventDef
  assert.ok(def.priority < ph.priority && ph.priority < cc.priority)
  assert.ok(def.priority > IDLE_EVENT_PRIORITY)
})

test('LU-2) LOOK_UP 은 아이들 이벤트 재생을 밀어낸다 (VOICE 가 홈/아이들을 선점)', () => {
  const d = decideTrigger(ctx({
    phase: 'EVENT_PLAYING', currentEventId: BLINK, requestedEventId: LU, hasSource: true,
  }))
  assert.equal(d.accepted, true)
})

test('LU-3) 진행 중인 액션은 밀어내지 못한다 (non-interruptible)', () => {
  for (const current of [CC, PH]) {
    const d = decideTrigger(ctx({
      phase: 'EVENT_PLAYING', currentEventId: current, requestedEventId: LU, hasSource: true,
    }))
    assert.deepEqual(d, { accepted: false, reason: 'busy-non-interruptible' }, current)
  }
})

test('LU-4) 소스 없으면 no-source, 스케줄러 후보에 절대 없다', () => {
  const d = decideTrigger(ctx({ requestedEventId: LU, hasSource: false }))
  assert.deepEqual(d, { accepted: false, reason: 'no-source' })
  assert.ok(registeredIdleEvents().every((e) => e.id !== LU))
})

test('LU-5) 센서 매핑 — 백엔드 TRIGGERS 의 거울', () => {
  assert.equal(sensorEventToRuntimeEvent('voice'), LU)
  assert.equal(sensorEventToRuntimeEvent('VOICE'), LU)
  assert.equal(sensorEventToRuntimeEvent('touch'), PH)
  assert.equal(sensorEventToRuntimeEvent('approach'), CC)
  assert.equal(sensorEventToRuntimeEvent('nfc_tagged'), null)  // 배경 경로의 일
  assert.equal(sensorEventToRuntimeEvent(''), null)
})

test('LU-6) 플레이 화면이 센서 스트림을 구독하고 매핑·적격성 게이트를 거친다', () => {
  const src = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')
  assert.match(src, /subscribePiRuntimeEvents/)
  assert.match(src, /sensorEventToRuntimeEvent/)
  assert.match(src, /isEligibleRef\.current\(id\)/)
  assert.match(src, /lookUpVideoUrl=\{lookUpSource\}/)
})


// ── 자세 상태 기계 (2026-09-08) — STANDING ↔ LYING ───────────────────────────

test('POSE-1) 자세는 현재 이벤트에서 파생된다', () => {
  assert.equal(poseForCurrentEvent(null), 'STANDING')          // BREATHING 홈
  assert.equal(poseForCurrentEvent(LD), 'LYING')               // 전이의 endPose
  assert.equal(poseForCurrentEvent(LI), 'LYING')               // 누운 홈
  assert.equal(poseForCurrentEvent(SU), 'STANDING')            // 복귀 전이
  assert.equal(poseForCurrentEvent(CC), 'STANDING')
  assert.equal(poseForCurrentEvent(BLINK), 'STANDING')
})

test('POSE-2) 서 있는 동안 STAND_UP 은 wrong-pose, 누운 동안 기존 전부 wrong-pose', () => {
  // IDLE(=BREATHING, STANDING) 에서 STAND_UP 은 자세가 틀렸다.
  assert.deepEqual(
    decideTrigger(ctx({ requestedEventId: SU, hasSource: true })),
    { accepted: false, reason: 'wrong-pose' })
  // LIE_IDLE 루프 중(LYING)에는 아이들/기존 액션 전부 자세가 틀렸다 —
  // 누운 개가 깜빡이러 일어나거나 다가오면 안 된다.
  for (const id of [BLINK, EAR, TILT, WAG, CC, PH, LU, LD]) {
    const d = decideTrigger(ctx({
      phase: 'EVENT_PLAYING', currentEventId: LI, requestedEventId: id, hasSource: true,
    }))
    assert.deepEqual(d, { accepted: false, reason: 'wrong-pose' }, id)
  }
})

test('POSE-3) 등록 정의 계약 — 체인/루프홈/스케줄러 격리', () => {
  const ld = RUNTIME_EVENTS[LD] as RuntimeEventDef
  const li = RUNTIME_EVENTS[LI] as RuntimeEventDef
  const su = RUNTIME_EVENTS[SU] as RuntimeEventDef
  assert.equal(ld.chainTo, LI)                    // 전이 → 누운 홈
  assert.equal(ld.returnToIdle, false)            // BREATH 복귀 없음
  assert.equal(li.loopUntilPreempted, true)       // 홈 루프
  assert.equal(li.interruptible, true)            // STAND_UP 이 밀어낼 수 있다
  assert.ok(su.priority > li.priority)
  assert.equal(su.returnToIdle, true)             // NEUTRAL_IDLE 포즈 → BREATHING
  assert.equal(su.returnPolicy, 'seam-aligned')
  // 셋 다 자발 스케줄러 후보가 아니다.
  assert.ok(registeredIdleEvents().every((e) => ![LD, SU, LI].includes(e.id)))
})

test('POSE-4) 전체 시퀀스: HOME → LIE_DOWN → LIE_IDLE → STAND_UP → HOME', () => {
  // 플레이어의 상태 전이를 순수 규칙으로 시뮬레이션한다. 각 단계에서
  // decideTrigger/체인 계약이 허용하는 것만 따라간다.
  const src = { hasSource: true }

  // 1) HOME(IDLE, STANDING): LIE_DOWN 트리거 수락.
  const d1 = decideTrigger(ctx({ requestedEventId: LD, ...src }))
  assert.equal(d1.accepted, true)

  // 2) LIE_DOWN 재생 중: 아무도 끼어들 수 없다 (non-interruptible).
  for (const id of [SU, CC, BLINK]) {
    const d = decideTrigger(ctx({
      phase: 'EVENT_PLAYING', currentEventId: LD, requestedEventId: id, ...src,
    }))
    assert.equal(d.accepted, false, id)
  }

  // 3) LIE_DOWN 종료 → 체인 계약이 LIE_IDLE 로 잇는다 (복귀 아님).
  const ld = RUNTIME_EVENTS[LD] as RuntimeEventDef
  assert.equal(ld.chainTo, LI)
  assert.equal(poseForCurrentEvent(LI), 'LYING')  // 이제 누운 홈이다

  // 4) LIE_IDLE 루프 중: STAND_UP 만 수락된다.
  const d4 = decideTrigger(ctx({
    phase: 'EVENT_PLAYING', currentEventId: LI, requestedEventId: SU, ...src,
  }))
  assert.equal(d4.accepted, true, 'STAND_UP 이 누운 홈을 선점해야 한다')

  // 5) STAND_UP 종료 → returnToIdle → BREATHING(HOME). 자세는 STANDING.
  const su = RUNTIME_EVENTS[SU] as RuntimeEventDef
  assert.equal(su.returnToIdle, true)
  assert.equal(poseForCurrentEvent(null), 'STANDING')
  // 그리고 서 있는 자세에서 다시 모든 기존 이벤트가 유효하다.
  assert.equal(decideTrigger(ctx({ requestedEventId: CC, ...src })).accepted, true)
  assert.equal(decideTrigger(ctx({ requestedEventId: LD, ...src })).accepted, true)
})

test('POSE-5) 플레이어 배선 — loop 속성과 체인 처리 (소스 스캔)', () => {
  const src = readFileSync('src/components/memorial/idle-loop-video.tsx', 'utf8')
  assert.match(src, /loop=\{def\.loopUntilPreempted === true\}/)
  assert.match(src, /chainFrom/)
  assert.match(src, /!state\.event\.loopUntilPreempted/)
})
