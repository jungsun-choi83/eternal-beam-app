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
/** 자산 스윕 — preview 인라인에서 빠져나온 공유 훅. 두 화면이 이것만 쓴다. */
const ASSETS = readFileSync('src/components/memorial/use-idle-event-assets.ts', 'utf8')
/** 프로덕션 재생 화면. preview 와 같은 배선을 갖되 dev 훅은 없어야 한다. */
const MEMORIAL = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')

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

// ── 자동 유료 생성 금지 ──────────────────────────────────────────────────────
//
// 확정된 사업 모델: 아이들 번들 1크레딧 / 액션 1크레딧. 화면을 열거나 확인을
// 누르는 것은 **결제가 아니다**. 예전에는 handleConfirm 이 COME_CLOSER + 첫 아이들
// 이벤트를 자동 제출했고(무과금 dev 엔드포인트라 가능했다), 그 코드가 그대로 남으면
// 유료 전환 즉시 사용자 동의 없는 결제가 된다.

test('확인(confirm)이 프리미엄 생성을 자동 착수하지 않는다', () => {
  const code = strip(SCREEN)
  assert.doesNotMatch(code, /ensureComeCloser\(/, '확인 경로가 COME_CLOSER 를 자동 생성한다')
  assert.doesNotMatch(
    code,
    /ensureIdleEventAsset\(/,
    '확인 경로가 아이들 이벤트를 자동 생성한다',
  )
})

test('어느 화면도 마운트만으로 유료 생성을 시작하지 않는다', () => {
  for (const [name, code] of [['preview', SCREEN], ['memorial', MEMORIAL]] as const) {
    const src = strip(code)
    assert.doesNotMatch(src, /ensureComeCloser\(/, `${name} 이 COME_CLOSER 를 자동 생성한다`)
    assert.doesNotMatch(src, /ensureIdleEventAsset\(/, `${name} 이 아이들 이벤트를 자동 생성한다`)
    assert.doesNotMatch(src, /purchasePremium\(/, `${name} 이 effect 에서 결제한다`)
  }
})

test('발견 경로는 조회 전용 함수만 쓴다', () => {
  assert.match(strip(ASSETS), /lookupIdleEventAsset\(/, '자산 훅이 조회 전용이 아니다')
  assert.doesNotMatch(strip(ASSETS), /ensureIdleEventAsset\(/, '자산 훅이 생성을 제출한다')
  for (const code of [SCREEN, MEMORIAL]) {
    assert.match(strip(code), /lookupComeCloserAsset\(/)
  }
})

test('구매 클라이언트는 발견과 분리돼 있고 결제 함수를 따로 노출한다', () => {
  const purchase = strip(readFileSync('src/lib/premium-assets.ts', 'utf8'))
  assert.match(purchase, /export async function discoverPremiumAssets/)
  assert.match(purchase, /export async function purchasePremium/)
  // 발견은 GET, 구매는 POST — 뒤바뀌면 조회가 결제가 된다.
  const discover = purchase.slice(purchase.indexOf('discoverPremiumAssets'))
  assert.match(discover.slice(0, 600), /method: "GET"/)
})

test('구매 요청은 인증 토큰을 보낸다 — 신원을 바디로 보내지 않는다', () => {
  const purchase = strip(readFileSync('src/lib/premium-assets.ts', 'utf8'))
  assert.match(purchase, /Authorization: `Bearer \$\{params\.accessToken\}`/)
  assert.doesNotMatch(purchase, /user_id:/, '신원을 바디에 실으면 서버가 토큰을 무시할 여지가 생긴다')
})

test('확인 전 프리미엄 생성 금지 — COME_CLOSER effect 와 자산 훅 둘 다 게이트된다', () => {
  // 스윕이 공유 훅으로 빠지면서 게이트도 그쪽으로 갔다. 불변식은 그대로다:
  // BREATH 가 없으면 어느 경로로도 유료 생성이 나가지 않는다.
  const code = strip(SCREEN)
  const gates = [...code.matchAll(/if \(!hasIdle\) return;/g)]
  assert.equal(gates.length, 1, `preview 의 COME_CLOSER hasIdle 게이트가 ${gates.length}개다`)
  assert.match(
    code,
    /useIdleEventAssets\(\{\s*pipeline,\s*enabled: hasIdle,/,
    'preview 가 자산 훅에 hasIdle 게이트를 넘기지 않는다',
  )
  assert.match(strip(ASSETS), /if \(!enabled\) return;/, '자산 훅에 enabled 게이트가 없다')
})

// ── 공유 훅 — 두 화면이 스윕을 복제하지 않는다 ───────────────────────────────

test('스윕 구현은 공유 훅에만 있다 — 화면에 복제하지 않는다', () => {
  // 이 루프는 유료 생성을 제출한다. 사본이 갈라지면 한쪽에서만 중복 지출이 난다.
  assert.match(strip(ASSETS), /IDLE_ASSET_SWEEP_MS/, '스윕 주기가 훅에 없다')
  for (const [name, code] of [['preview', SCREEN], ['memorial', MEMORIAL]] as const) {
    assert.doesNotMatch(
      strip(code),
      /const sweep = async/,
      `${name} 에 스윕 사본이 남아 있다`,
    )
  }
  // memorial 은 자산 확보를 직접 하지 않는다 — 훅을 통해서만 한다.
  assert.doesNotMatch(strip(MEMORIAL), /ensureIdleEventAsset/, 'memorial 이 직접 제출하고 있다')
})

test('공유 훅도 이벤트 id 를 하드코딩하지 않는다', () => {
  const code = strip(ASSETS)
  for (const id of ['BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING']) {
    assert.doesNotMatch(code, new RegExp(`["']${id}["']`), `${id} 를 하드코딩했다`)
  }
  assert.match(code, /registeredIdleEvents\(\)/, '레지스트리를 순회하지 않는다')
})

test('후보 목록은 READY URL 에서만 나온다', () => {
  // 빈 문자열/누락을 후보로 넣으면 스케줄러가 마운트되지 않은 이벤트를 고른다.
  assert.match(
    strip(ASSETS),
    /typeof url === "string" && url\.length > 0/,
    '빈 URL 을 후보에서 걸러내지 않는다',
  )
})

// ── memorial-device-play-screen — preview 와 같은 배선 ───────────────────────

test('memorial 이 공유 자산 훅과 스케줄러를 쓴다', () => {
  const code = strip(MEMORIAL)
  assert.match(code, /useIdleEventAssets\(\{/, '자산 훅을 쓰지 않는다')
  assert.match(code, /useIdleEventScheduler\(\{/, '스케줄러를 붙이지 않았다')
  // Phase 6: 원본이 아니라 **적격한 것만** 넘긴다 (구독 ∩ READY ∩ ON).
  assert.match(code, /idleEventSources=\{eligibleIdleEventSources\}/, '적격 소스 표를 넘기지 않는다')
})

test('memorial 의 스케줄러도 같은 트리거 핸들을 쓴다', () => {
  // 진입점이 갈라지면 우선순위·이음매 정책이 한쪽에만 적용된다.
  assert.match(strip(MEMORIAL), /triggerRef: comeCloserTriggerRef/)
})

test('memorial 이 busy 신호를 연결한다 — 없으면 COME_CLOSER 중에도 발화한다', () => {
  // 이 prop 이 없으면 busyRef 가 영영 true 가 되지 않아 스케줄러가 COME_CLOSER
  // 재생 중에 트리거하고, 거절 → 재예약 루프를 돈다.
  assert.match(strip(MEMORIAL), /onActionStateChange=\{onPlaybackStateChange\}/)
})

test('memorial 의 게이트는 실제 BREATH 자산이다 — 데모 폴백이 아니다', () => {
  const code = strip(MEMORIAL)
  assert.match(code, /const hasIdle = hasRealIdleVideo\(pipeline\)/, 'hasRealIdleVideo 게이트가 없다')
  assert.match(code, /useIdleEventAssets\(\{\s*pipeline,\s*enabled: hasIdle,/)
  // petIdleSrc 는 resolveIdleVideoUrl 을 거쳐 데모 mp4 일 수 있다 — 게이트 근거로 금지.
  assert.doesNotMatch(code, /enabled: Boolean\(petIdleSrc\)|enabled: !!petIdleSrc/)
})

test('dev 수동 트리거는 preview 전용 — 프로덕션 화면에 없다', () => {
  // 양쪽이 같은 window 전역을 설치하면 나중에 언마운트되는 쪽이 상대의 핸들을
  // 지운다(cleanup 이 무조건 delete 한다). 제품 가치는 0 이다.
  for (const hook of [
    '__ebBlink', '__ebEarTwitch', '__ebHeadTilt', '__ebTailWag', '__ebIdleEvent',
  ]) {
    assert.ok(!MEMORIAL.includes(hook), `${hook} 이 memorial 에 새어 들어갔다`)
  }
})
