/**
 * "Unlock More Features" — 가격·상태 계약.
 *
 * 확정된 사업 모델:
 *   BREATHING          무료
 *   IDLE_BUNDLE        1 크레딧 (등록된 자발적 아이들 이벤트 **전체**)
 *   COME_CLOSER        1 크레딧 (더블탭)
 *   둘 다 없으면 총 2 크레딧, 한쪽만 없으면 1, 둘 다 있으면 0.
 *
 * 표시 금액은 예상치이고 실제 과금은 서버가 정한다 — 다만 예상이 틀리면 사용자가
 * 잘못된 가격을 보고 결제하므로, 여기서 규칙을 못 박는다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  COME_CLOSER_ACTION,
  deriveUnlockState,
  isComeCloserReady,
  readyIdleEventIds,
} from './premium-unlock.ts'
import { KIND_IDLE_BUNDLE, actionKind } from './premium-assets.ts'
import type { PremiumAssets } from './premium-assets.ts'

const IDLE = ['BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING']

function assets(over: Partial<PremiumAssets> = {}): PremiumAssets {
  return {
    petId: 'pet1',
    ready: {},
    generating: [],
    missing: [...IDLE, COME_CLOSER_ACTION],
    idleEvents: IDLE,
    actionEvents: [COME_CLOSER_ACTION],
    idleBundleCredits: 1,
    actionEventCredits: 1,
    // 이 파일은 크레딧 시대의 가격/상태 계산을 고정한다. 구독 필드는 그 계산에
    // 관여하지 않으므로(deriveUnlockState 는 읽지 않는다) 기본값으로 채운다.
    entitled: true,
    subscriptionStatus: 'active',
    subscriptionRequired: true,
    ...over,
  }
}

const url = (id: string) => `https://cdn.test/${id}.mp4`
const allIdleReady = () => Object.fromEntries(IDLE.map((i) => [i, url(i)]))

const derive = (a: PremiumAssets | null, balance: number | null = 10) =>
  deriveUnlockState({ assets: a, balance, hasAuth: true })

// ── 가격 ─────────────────────────────────────────────────────────────────────

test('둘 다 없으면 2 크레딧', () => {
  const s = derive(assets())
  assert.equal(s.requiredCredits, 2)
  assert.equal(s.phase, 'purchasable')
  assert.deepEqual(s.missingKinds, [KIND_IDLE_BUNDLE, actionKind(COME_CLOSER_ACTION)])
})

test('아이들 번들만 보유 → COME_CLOSER 1 크레딧', () => {
  const s = derive(assets({ ready: allIdleReady(), missing: [COME_CLOSER_ACTION] }))
  assert.equal(s.requiredCredits, 1)
  assert.equal(s.idle.state, 'ready')
  assert.equal(s.comeCloser.state, 'missing')
  assert.deepEqual(s.missingKinds, [actionKind(COME_CLOSER_ACTION)])
})

test('COME_CLOSER 만 보유 → 아이들 번들 1 크레딧', () => {
  const s = derive(
    assets({ ready: { [COME_CLOSER_ACTION]: url('cc') }, missing: [...IDLE] })
  )
  assert.equal(s.requiredCredits, 1)
  assert.equal(s.comeCloser.state, 'ready')
  assert.equal(s.idle.state, 'missing')
  assert.deepEqual(s.missingKinds, [KIND_IDLE_BUNDLE])
})

test('둘 다 보유 → 0 크레딧, 구매 CTA 없음', () => {
  const s = derive(
    assets({ ready: { ...allIdleReady(), [COME_CLOSER_ACTION]: url('cc') }, missing: [] })
  )
  assert.equal(s.requiredCredits, 0)
  assert.equal(s.phase, 'unlocked')
  assert.deepEqual(s.missingKinds, [])
})

test('부분 보유 아이들 번들도 1 크레딧 — 이벤트당 과금이 아니다', () => {
  const s = derive(
    assets({
      ready: { BLINKING: url('b'), TAIL_WAGGING: url('t') },
      missing: ['EAR_TWITCHING', 'HEAD_TILTING', COME_CLOSER_ACTION],
    })
  )
  assert.equal(s.idle.credits, 1, '누락 2건이라고 2 크레딧을 받으면 안 된다')
  assert.equal(s.requiredCredits, 2)
  assert.equal(s.idle.readyCount, 2)
  assert.equal(s.idle.totalCount, 4)
})

test('레지스트리가 5종이 되어도 번들은 여전히 1 크레딧', () => {
  const five = [...IDLE, 'NOSE_WIGGLE']
  const s = derive(
    assets({ idleEvents: five, missing: [...five, COME_CLOSER_ACTION] })
  )
  assert.equal(s.idle.credits, 1)
  assert.equal(s.idle.totalCount, 5)
  assert.equal(s.requiredCredits, 2)
})

// ── 생성 중은 무료 ───────────────────────────────────────────────────────────

test('생성 중인 쪽은 과금 대상이 아니다 (서버 가드와 같은 판정)', () => {
  const s = derive(
    assets({ generating: [...IDLE], missing: [COME_CLOSER_ACTION] })
  )
  assert.equal(s.idle.state, 'generating')
  assert.equal(s.idle.credits, 0)
  assert.equal(s.requiredCredits, 1, '생성 중인 번들에 다시 과금하려 한다')
})

test('전부 생성 중이면 unlocking — 살 것이 없다', () => {
  const s = derive(
    assets({ generating: [...IDLE, COME_CLOSER_ACTION], missing: [] })
  )
  assert.equal(s.phase, 'unlocking')
  assert.equal(s.requiredCredits, 0)
  assert.deepEqual(s.missingKinds, [])
})

// ── 잔액 ─────────────────────────────────────────────────────────────────────

test('잔액 부족이면 구매를 막는다', () => {
  const s = derive(assets(), 1)
  assert.equal(s.requiredCredits, 2)
  assert.equal(s.phase, 'insufficient-credits')
})

test('필요액과 잔액이 같으면 구매 가능', () => {
  assert.equal(derive(assets(), 2).phase, 'purchasable')
})

test('잔액 0이어도 이미 보유한 것은 잠금 해제 상태로 남는다', () => {
  const s = derive(
    assets({ ready: { ...allIdleReady(), [COME_CLOSER_ACTION]: url('cc') }, missing: [] }),
    0
  )
  assert.equal(s.phase, 'unlocked', '잔액 0이 보유 자산을 잠갔다')
  assert.equal(s.requiredCredits, 0)
})

test('잔액을 모르면(null) 구매를 막지 않는다 — 서버가 최종 판정한다', () => {
  assert.equal(derive(assets(), null).phase, 'purchasable')
})

// ── 인증 ─────────────────────────────────────────────────────────────────────

test('토큰이 없으면 signed-out — 구매 대상이 없다', () => {
  const s = deriveUnlockState({ assets: assets(), balance: 10, hasAuth: false })
  assert.equal(s.phase, 'signed-out')
})

// ── 재생 가능 여부는 READY 자산만으로 정해진다 ───────────────────────────────

test('READY 아이들 이벤트가 스케줄러 후보가 된다', () => {
  const a = assets({ ready: { BLINKING: url('b'), HEAD_TILTING: url('h') } })
  assert.deepEqual(readyIdleEventIds(a).sort(), ['BLINKING', 'HEAD_TILTING'])
})

test('READY COME_CLOSER 만 더블탭을 활성화한다', () => {
  assert.equal(isComeCloserReady(assets()), false)
  assert.equal(isComeCloserReady(assets({ generating: [COME_CLOSER_ACTION] })), false)
  assert.equal(
    isComeCloserReady(assets({ ready: { [COME_CLOSER_ACTION]: url('cc') } })),
    true
  )
})

test('잔액 0이어도 재생 후보 판정은 바뀌지 않는다', () => {
  const a = assets({ ready: allIdleReady() })
  // 잔액과 무관한 순수 함수여야 한다 — 인자로 잔액을 받지도 않는다.
  assert.equal(readyIdleEventIds(a).length, 4)
})

// ── 소스 수준 가드 ───────────────────────────────────────────────────────────

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const HOOK = readFileSync('src/components/memorial/use-premium-unlock.ts', 'utf8')

// unlock-features-card.tsx 는 Phase 3 에서 Monthly Membership 으로 교체돼 삭제됐다.
// 이 훅은 행동별 선택 UI(Behavior Library)를 위해 남겨 둔 것이므로, 여기서는
// **훅 자체의 안전 계약**만 계속 고정한다 — 카드가 없다고 규칙이 느슨해지지 않는다.

test('화면을 여는 것만으로 결제되지 않는다 — effect 는 GET 만 한다', () => {
  const code = strip(HOOK)
  const effects = [...code.matchAll(/useEffect\(\(\) => \{([\s\S]*?)\n  \}, \[/g)]
  assert.ok(effects.length >= 1)
  for (const m of effects) {
    assert.doesNotMatch(m[1], /purchasePremium\(/, 'effect 안에서 결제한다')
    assert.doesNotMatch(m[1], /\bpurchase\(\)/, 'effect 가 구매를 자동 실행한다')
  }
})

test('훅은 구매를 스스로 실행하지 않는다 — 호출은 언제나 밖에서 온다', () => {
  const code = strip(HOOK)
  // purchase 는 반환되기만 한다. 훅 안에서 스스로 부르는 곳이 있으면 안 된다.
  const body = code.slice(code.indexOf('const purchase = useCallback'))
  const selfCalls = body.match(/^\s*(void )?purchase\(\);?$/gm) ?? []
  assert.equal(selfCalls.length, 0, '훅이 스스로 구매를 실행한다')
})

test('한 번의 클릭이 두 번 제출되지 않는다 (ref 가드)', () => {
  const code = strip(HOOK)
  assert.match(code, /inflightRef/, '중복 제출 가드가 없다')
  const body = code.slice(code.indexOf('const purchase = useCallback'))
  assert.match(body.slice(0, 300), /if \(inflightRef\.current\) return;/)
})

test('살 것이 없으면 구매를 호출조차 하지 않는다', () => {
  const body = strip(HOOK).slice(strip(HOOK).indexOf('const purchase = useCallback'))
  assert.match(body, /current\.missingKinds\.length === 0\) return;/)
})

test('두 구매는 독립이다 — 하나가 실패해도 롤백하지 않는다', () => {
  const code = strip(HOOK)
  assert.match(code, /Promise\.allSettled\(/, '한 쪽 실패가 다른 쪽을 취소하면 안 된다')
  assert.doesNotMatch(code, /rollback|refund/i, 'UI 가 롤백을 시도한다')
})

test('missing 인 쪽만 POST 한다 — 재시도가 보유분을 다시 사지 않는다', () => {
  const code = strip(HOOK)
  assert.match(code, /current\.missingKinds\.map\(\(kind\) =>/)
})

test('실제 차감액은 서버 응답에서 읽는다', () => {
  assert.match(strip(HOOK), /charged \+= r\.value\.creditsCharged/)
})

test('재생 계층에는 지갑 검사가 없다', () => {
  for (const f of [
    'src/components/memorial/idle-loop-video.tsx',
    'src/components/memorial/use-idle-event-scheduler.ts',
    'src/lib/pet-runtime-events.ts',
    'src/components/memorial/pet-idle-display.tsx',
  ]) {
    const code = strip(readFileSync(f, 'utf8'))
    for (const needle of ['credits', 'wallet', 'purchase', 'balance']) {
      assert.doesNotMatch(code, new RegExp(needle, 'i'), `${f} 에 결제 관심사가 새어 들어갔다`)
    }
  }
})

test('레거시 4종은 잠금 해제 모델에 등장하지 않는다', () => {
  const code = strip(HOOK) + strip(readFileSync('src/lib/premium-unlock.ts', 'utf8'))
  for (const legacy of ['TOUCH', 'VOICE', 'NFC']) {
    assert.doesNotMatch(
      code,
      new RegExp(`["']${legacy}["']`),
      `레거시 ${legacy} 가 웹 프리미엄 모델에 섞였다`,
    )
  }
})
