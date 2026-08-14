/**
 * 런타임 이벤트 자동재생 방지 — 소스 수준 회귀 가드.
 *
 * 증상: URL 이 로드되자마자 사용자 조작 없이 COME_CLOSER 가 재생됐다.
 * 요구 동작: 이벤트 소스는 **프리로드만** 하고 정지 상태(currentTime=0)로 대기하며,
 * BREATHING 은 계속 재생된다. trigger(id) 만이 재생을 시작한다.
 *
 * DOM 없이 컴포넌트를 렌더할 수 없으므로(jsdom 미사용) 불변식을 소스에서 고정한다.
 *
 * 식별자 이력 — **불변식은 한 번도 바뀌지 않았고** 가리키는 이름만 옮겨 왔다:
 *   Phase 0 이전: actionVideoRef / actionPlayingRef / action.play()
 *   Phase 0     : 레지스트리 + playbackRef 유니온 + actionEl.play()
 *   Phase 0.5   : ACTION_* 단계 → EVENT_* (아이들 이벤트도 같은 경로를 쓰므로,
 *                 ACTION_ 이라고 부르면 눈 깜빡임을 액션으로 취급하게 된다)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { RUNTIME_EVENTS } from './pet-runtime-events.ts'

const SRC = readFileSync('src/components/memorial/idle-loop-video.tsx', 'utf8')

/** 액션 <video> JSX 블록만 잘라 낸다. */
function actionVideoBlock(): string {
  const i = SRC.indexOf('ref={(el) => setActionEl(def.id, el)}')
  assert.ok(i > 0, '액션 비디오 엘리먼트를 찾지 못했다')
  return SRC.slice(i, SRC.indexOf('/>', i))
}

test('액션 비디오는 autoPlay 를 명시적으로 끈다', () => {
  const block = actionVideoBlock()
  assert.match(block, /autoPlay=\{false\}/, 'autoPlay={false} 가 명시돼야 한다')
  assert.doesNotMatch(block, /^\s*autoPlay\s*$/m, '무조건 autoPlay 속성이 있으면 안 된다')
})

test('액션 비디오는 loop 하지 않는다 (1회 재생)', () => {
  assert.doesNotMatch(actionVideoBlock(), /^\s*loop\s*$/m)
})

test('이벤트 비디오는 preload 만 한다 — 정책은 이벤트 정의가 쥔다', () => {
  assert.match(actionVideoBlock(), /preload=\{def\.preload\}/)
  // COME_CLOSER 의 실제 정책은 예전 하드코딩된 preload="auto" 와 같아야 한다.
  assert.equal(RUNTIME_EVENTS.COME_CLOSER?.preload, 'auto')
})

test('새로 등록될 이벤트가 기본으로 프리로드되지 않는다 (COME_CLOSER 만 auto)', () => {
  const autos = Object.values(RUNTIME_EVENTS)
    .filter((d) => d !== undefined)
    .filter((d) => d.preload === 'auto')
  assert.deepEqual(autos.map((d) => d.id), ['COME_CLOSER'])
})

test('BREATH(아이들)는 계속 autoPlay + loop 여야 한다', () => {
  const i = SRC.indexOf('ref={idleVideoRef}')
  const block = SRC.slice(i, SRC.indexOf('/>', i))
  assert.match(block, /^\s*autoPlay\s*$/m, 'BREATH 는 자동재생이어야 한다')
  assert.match(block, /^\s*loop\s*$/m, 'BREATH 는 반복재생이어야 한다')
})

test('사용자 조작 전까지 정지 상태로 고정하는 가드가 있다', () => {
  assert.match(SRC, /const pinPaused = \(\) => \{/)
  // 예전 `if (actionPlayingRef.current) return;` 과 같은 역할 —
  // 재생 중인 액션에는 손대지 않는다.
  assert.match(
    SRC,
    /if \(state\.phase === "EVENT_PLAYING" && state\.event\.id === id\) return;/,
    '재생 중인 액션을 정지시키지 않는 가드가 필요하다',
  )
  assert.match(SRC, /actionEl\.pause\(\)/)
  assert.match(SRC, /actionEl\.currentTime = 0/)
  // 4개 이벤트 전부에서 정지를 강제해야 한다 (이제 배열 순회로 등록한다).
  assert.match(
    SRC,
    /const events = \["loadedmetadata", "loadeddata", "canplay", "play"\] as const;/,
    '프리로드 중 재생으로 이어지는 4개 이벤트를 모두 감시해야 한다',
  )
  assert.match(SRC, /for \(const ev of events\) actionEl\.addEventListener\(ev, pinPaused\);/)
})

test('복귀 전환 중 도착 프레임을 되감지 않는다', () => {
  assert.match(
    SRC,
    /if \(state\.phase === "EVENT_RETURNING" && state\.event\.id === id\) return;/,
    'hold 중 currentTime=0 으로 되돌리면 클로즈업 대신 첫 프레임이 나온다',
  )
})

test('캔버스 활성 소스는 trigger 안에서만 액션으로 바뀐다', () => {
  const assigns = [...SRC.matchAll(/videoRef\.current = (\w+)/g)].map((m) => m[1])
  assert.ok(assigns.includes('actionEl'), 'trigger 가 액션으로 전환해야 한다')
  // 'actionEl' 로의 전환은 정확히 한 곳(trigger)이어야 한다.
  assert.equal(
    assigns.filter((a) => a === 'actionEl').length, 1,
    '액션으로의 전환 지점이 여러 곳이면 자동재생 회귀가 쉽다',
  )
})

test('play() 는 trigger 안의 actionEl.play() 하나뿐이다', () => {
  const actionPlays = [...SRC.matchAll(/actionEl\s*\n?\s*\.play\(\)|actionEl\.play\(\)/g)]
  assert.equal(actionPlays.length, 1, '액션 재생 진입점은 하나여야 한다')
})

test('트리거 진입 규칙은 레지스트리 정책을 거친다 — 컴포넌트에 흩어지지 않는다', () => {
  assert.match(SRC, /const decision = decideTrigger\(\{/, 'decideTrigger 로 판정해야 한다')
  assert.match(SRC, /if \(!decision\.accepted\)/)
})

// ── Phase 1A — 아이들 이벤트 진입 정책 / 자발적 스케줄링 금지 ────────────────

test('1A) 이음매 대기는 정책이 wait-for-seam 인 이벤트에만 적용된다', () => {
  assert.match(
    SRC,
    /if \(def\.entryPolicy === "immediate"\)/,
    'entryPolicy 로 분기해야 한다 — 액션은 절대 기다리면 안 된다',
  )
  assert.match(SRC, /phase: "EVENT_PENDING_SEAM"/)
})

test('1A) 이음매 대기에는 상한이 있다 — 이벤트를 조용히 삼키지 않는다', () => {
  assert.match(SRC, /waited >= SEAM_WAIT_MAX_MS/, '무한 대기는 이벤트를 삼킨다')
})

test('1A) 복귀 프로파일은 이벤트 정의에서 온다 — 하드코딩된 상수가 아니다', () => {
  assert.match(SRC, /returnProfileFor\(def\.returnPolicy\)/)
  assert.match(
    SRC,
    /profile\.scaleBridge\s*\n?\s*\?/,
    '배율 실측은 배율 브리지를 쓰는 정책에서만 해야 한다',
  )
})

test('1A) 선점 시 이전 이벤트의 <video> 를 반드시 멈춘다', () => {
  // 안 멈추면 화면 밖에서 계속 돌다가 ended 를 쏘고, 그 ended 가 새 이벤트를
  // 복귀시킨다 — COME_CLOSER 가 BLINKING 을 밀어낸 직후 스스로 끝나 버린다.
  assert.match(SRC, /prevEl\.pause\(\)/)
  assert.match(SRC, /prevEl\.currentTime = 0/)
})

test('7) 플레이어 자신은 스케줄링하지 않는다 — 결정은 바깥에서 한다', () => {
  // Phase 3 에서 자발적 스케줄러가 생겼지만 **플레이어 밖**이다
  // (use-idle-event-scheduler.ts). 이 경계가 무너지면 "무엇을 언제 틀지"가
  // 재생 로직과 뒤섞여 우선순위·이음매 규칙을 우회하는 경로가 생긴다.
  const code = SRC.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
  assert.doesNotMatch(code, /setInterval/, '주기 타이머 = 플레이어 내부 스케줄링')
  assert.doesNotMatch(code, /Math\.random/, '무작위 선택 = 플레이어 내부 스케줄링')
  assert.doesNotMatch(code, /registeredIdleEvents/, '후보 열거는 플레이어의 일이 아니다')
  const selfCalls = [...code.matchAll(/(?<![.\w])trigger\((?!\{)/g)]
  assert.equal(selfCalls.length, 0, `플레이어가 스스로 trigger() 를 호출한다 (${selfCalls.length}곳)`)
})
