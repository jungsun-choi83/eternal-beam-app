/**
 * 스케줄러 배선 — 소스 수준 회귀 가드.
 *
 * 실행: npm test
 *
 * jsdom 이 없어 훅을 렌더할 수 없으므로, 타이머 관련 불변식을 소스에서 고정한다.
 * 여기서 지키는 것들은 전부 "겹침/큐잉/멈춤" 부류의 버그인데, 실제로 터지면
 * 재현이 어렵다(수 초~수십 초 뒤에 한 번씩 일어난다).
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const HOOK = readFileSync('src/components/memorial/use-idle-event-scheduler.ts', 'utf8')
const SCREEN = readFileSync('src/components/memorial/preview-screen.tsx', 'utf8')
const PLAYER = readFileSync('src/components/memorial/idle-loop-video.tsx', 'utf8')

/** 주석을 제거한 코드 — 설명 문구가 매칭에 섞이지 않게. */
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

// ── 4) 동시에 하나만 — 겹침/큐잉 불가 ───────────────────────────────────────

test('4) 타이머 핸들은 하나뿐이다 — 여러 개면 이벤트가 겹칠 수 있다', () => {
  const code = strip(HOOK)
  const timerDecls = [...code.matchAll(/const \w*[tT]imerRef = useRef/g)]
  assert.equal(timerDecls.length, 1, `타이머 ref 가 ${timerDecls.length} 개다`)
  // 큐/배열로 예약을 쌓는 코드가 없어야 한다.
  assert.doesNotMatch(code, /queue|pending\s*:\s*\[|\.push\(/i, '예약을 큐에 쌓고 있다')
})

test('4b) setTimeout 은 언제나 기존 타이머를 버린 뒤에만 건다', () => {
  const code = strip(HOOK)
  // setTimeout 을 timerRef 에 담는 지점마다 그 앞에 clearTimer() 가 있어야 한다.
  const assigns = [...code.matchAll(/timerRef\.current = window\.setTimeout/g)]
  assert.ok(assigns.length >= 1)
  for (const m of assigns) {
    const before = code.slice(Math.max(0, m.index! - 400), m.index!)
    assert.match(before, /clearTimer\(\)/, 'clearTimer() 없이 타이머를 걸었다')
  }
})

// ── 7 / 9) COME_CLOSER 와의 상호작용 ─────────────────────────────────────────

test('7) 재생이 시작되면 예약된 자발적 기회를 **버린다** (큐잉하지 않는다)', () => {
  const code = strip(HOOK)
  const i = code.indexOf('if (playing)')
  assert.ok(i > 0, 'playing 분기를 찾지 못했다')
  // 분기 끝(첫 return)까지만 잘라 본다 — 뒤쪽 쿨다운 재예약이 섞이면 안 된다.
  const end = code.indexOf('return;', i)
  assert.ok(end > i, 'playing 분기의 끝을 찾지 못했다')
  const branch = code.slice(i, end)
  assert.match(branch, /clearTimer\(\)/, 'playing=true 에서 타이머를 버려야 한다')
  assert.doesNotMatch(branch, /arm\(/, 'playing=true 에서 다시 예약하면 안 된다')
})

test('9) 재생 중에는 예약도 발화도 하지 않는다', () => {
  const code = strip(HOOK)
  // arm(): busy 면 타이머를 걸지 않는다.
  const armBody = code.slice(code.indexOf('const arm = useCallback'), code.indexOf('const fire = useCallback'))
  assert.match(armBody, /if \(busyRef\.current\) return;/, 'arm 에 busy 가드가 없다')
  // fire(): busy 면 트리거하지 않는다.
  const fireBody = code.slice(code.indexOf('const fire = useCallback'), code.indexOf('fireRef.current = fire'))
  assert.match(fireBody, /busyRef\.current/, 'fire 에 busy 가드가 없다')
})

test('9b) busy 신호는 재생 상태 알림에서만 온다 — 스케줄러가 추측하지 않는다', () => {
  assert.match(strip(HOOK), /busyRef\.current = playing;/)
  assert.match(SCREEN, /onActionStateChange=\{onPlaybackStateChange\}/, '화면이 알림을 연결하지 않았다')
})

test('복귀 후에는 쿨다운으로 다시 예약한다 — 스케줄러가 멈추지 않는다', () => {
  const code = strip(HOOK)
  const i = code.indexOf('awaitingRef.current = null;\n      arm(nextCooldownDelayMs')
  assert.ok(i > 0, 'playing=false 경로에서 쿨다운 재예약이 없다')
})

test('트리거가 거절돼도 스케줄러가 멈추지 않는다 (검증 타이머)', () => {
  // 거절되면 재생 알림이 오지 않는다. 이 타이머가 없으면 영영 예약이 안 걸린다.
  assert.match(strip(HOOK), /IDLE_TRIGGER_VERIFY_MS/)
})

// ── 후보 출처 ────────────────────────────────────────────────────────────────

test('스케줄러는 이벤트 id 를 하드코딩하지 않는다', () => {
  const code = strip(HOOK) + strip(readFileSync('src/lib/idle-event-scheduler.ts', 'utf8'))
  for (const id of [
    'BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING', 'COME_CLOSER',
  ]) {
    assert.doesNotMatch(
      code,
      new RegExp(`["']${id}["']`),
      `${id} 를 하드코딩했다 — registeredIdleEvents() 가 유일한 출처여야 한다`
    )
  }
})

// ── 10) 수동 트리거 유지 ─────────────────────────────────────────────────────

test('10) 수동 dev 트리거가 그대로 살아 있다', () => {
  for (const hook of [
    '__ebBlink', '__ebEarTwitch', '__ebHeadTilt', '__ebTailWag', '__ebIdleEvent',
  ]) {
    assert.ok(SCREEN.includes(hook), `${hook} 이 사라졌다`)
  }
})

test('10b) 수동과 자발적 트리거는 같은 핸들을 쓴다', () => {
  // 진입점이 갈라지면 정책(우선순위·이음매)이 한쪽에만 적용된다.
  assert.match(SCREEN, /triggerRef: comeCloserTriggerRef/)
  assert.match(SCREEN, /const fire = comeCloserTriggerRef\.current/)
})

// ── 플레이어는 여전히 스스로 스케줄하지 않는다 ───────────────────────────────

test('플레이어에는 스케줄링이 없다 — 결정은 전부 바깥에서 한다', () => {
  const code = strip(PLAYER)
  assert.doesNotMatch(code, /setInterval/)
  assert.doesNotMatch(code, /Math\.random/)
  assert.doesNotMatch(code, /registeredIdleEvents/)
  const selfCalls = [...code.matchAll(/(?<![.\w])trigger\((?!\{)/g)]
  assert.equal(selfCalls.length, 0, '플레이어가 스스로 trigger() 를 호출한다')
})
