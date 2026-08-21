/**
 * Monthly Membership — 상태 모델과 배선 계약.
 *
 * 크레딧 모델을 대체한 뒤 사용자에게 남는 질문은 하나다: **지금 멤버인가.**
 * 그리고 그보다 중요한 것: 멤버가 아니게 되어도 **잃는 것이 없어야 한다.**
 *
 * 여기서 못 박는 것:
 *   * entitled 는 서버가 정한다 — 프론트가 유예 기간을 다시 계산하지 않는다.
 *   * 만료는 자산을 지우지 않고, BREATHING 은 언제나 재생된다.
 *   * 멤버십 UI 는 아무것도 생성하지 않는다 (Behavior Library 는 다음 단계).
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

import {
  deriveMembershipState,
  keepsExistingAssets,
  type MembershipInput,
} from './membership.ts'

const derive = (over: Partial<MembershipInput> = {}) =>
  deriveMembershipState({ status: null, entitled: false, hasAuth: true, ...over })

// ── 상태 판정 ────────────────────────────────────────────────────────────────

test('로그인 전에는 가입 권유조차 하지 않는다 — 멤버십은 계정에 묶인다', () => {
  const s = derive({ hasAuth: false, entitled: true, status: 'active' })
  assert.equal(s.phase, 'signed-out')
  assert.equal(s.canGenerate, false)
  assert.equal(s.showJoinCta, false)
})

test('구독 이력이 없으면 none', () => {
  const s = derive()
  assert.equal(s.phase, 'none')
  assert.equal(s.showJoinCta, true)
})

test('active + entitled → 이용 중', () => {
  const s = derive({ status: 'active', entitled: true })
  assert.equal(s.phase, 'active')
  assert.equal(s.canGenerate, true)
  assert.equal(s.showJoinCta, false, '이용 중인데 가입을 권한다')
})

test('해지했지만 기간이 남았으면 grace — 여전히 이용 가능하다', () => {
  const s = derive({ status: 'canceled', entitled: true })
  assert.equal(s.phase, 'grace')
  assert.equal(s.canGenerate, true)
  assert.equal(s.showJoinCta, false)
})

test('만료되면 lapsed — 생성만 막힌다', () => {
  const s = derive({ status: 'expired', entitled: false })
  assert.equal(s.phase, 'lapsed')
  assert.equal(s.canGenerate, false)
  assert.equal(s.showJoinCta, true)
})

test('해지 후 기간까지 끝났으면 lapsed', () => {
  const s = derive({ status: 'canceled', entitled: false })
  assert.equal(s.phase, 'lapsed')
})

test('entitled 는 서버가 정한다 — status 만으로 뒤집지 않는다', () => {
  // 서버가 active 인데 entitled=false 라고 하면 서버를 따른다(환불·분쟁 등).
  const s = derive({ status: 'active', entitled: false })
  assert.equal(s.canGenerate, false, '프론트가 서버 판정을 덮어썼다')
})

// ── 만료돼도 잃는 것이 없다 ──────────────────────────────────────────────────

test('만료 상태에서 보유 자산 수를 그대로 들고 있다', () => {
  const s = derive({ status: 'expired', entitled: false, readyCount: 3 })
  assert.equal(s.readyCount, 3, '만료가 자산 수를 0으로 만들었다')
  assert.equal(keepsExistingAssets(s), true)
})

test('보유 자산이 없으면 "남아 있다" 문구를 띄우지 않는다', () => {
  assert.equal(keepsExistingAssets(derive({ status: 'expired', readyCount: 0 })), false)
})

test('이용 중일 때는 만료 안내 문구를 띄우지 않는다', () => {
  const s = derive({ status: 'active', entitled: true, readyCount: 5 })
  assert.equal(keepsExistingAssets(s), false)
})

// ── 소스 수준 가드 ───────────────────────────────────────────────────────────

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const CARD = readFileSync('src/components/memorial/membership-card.tsx', 'utf8')
const SECTION = readFileSync('src/components/memorial/membership-section.tsx', 'utf8')
const HOOK = readFileSync('src/components/memorial/use-membership.ts', 'utf8')
// Phase 5 에서 조회·폴링이 공유 Provider 로 옮겨졌다 (중복 폴링 제거).
// GET-only 계약은 사라진 게 아니라 **그쪽으로 이동**했으므로 거기서 검사한다.
const PROVIDER = readFileSync('src/components/memorial/premium-assets-context.tsx', 'utf8')

test('멤버십 UI 는 아무것도 생성하지 않는다 — Behavior Library 는 다음 단계다', () => {
  for (const [name, src] of [['card', CARD], ['section', SECTION], ['hook', HOOK]] as const) {
    const code = strip(src)
    assert.doesNotMatch(code, /purchasePremium\(/, `${name} 가 생성을 제출한다`)
    assert.doesNotMatch(code, /submit|generate/i, `${name} 에 생성 관심사가 새어 들어갔다`)
  }
})

test('멤버십 UI 에 크레딧·잔액 개념이 없다', () => {
  for (const [name, src] of [['card', CARD], ['section', SECTION], ['hook', HOOK]] as const) {
    const code = strip(src)
    for (const needle of ['credit', 'wallet', 'balance', '크레딧']) {
      assert.doesNotMatch(code, new RegExp(needle, 'i'), `${name} 에 ${needle} 이 남아 있다`)
    }
  }
})

test('공유 조회원은 GET 만 한다 — 마운트가 결제나 생성을 일으키지 않는다', () => {
  const code = strip(PROVIDER)
  assert.match(code, /discoverPremiumAssets\(/, '자산 조회를 하지 않는다')
  assert.doesNotMatch(code, /method:\s*["']POST["']/, 'Provider 가 POST 를 한다')
  assert.doesNotMatch(code, /purchasePremium\(/, 'Provider 가 생성을 제출한다')
})

test('멤버십 훅은 스스로 네트워크를 타지 않는다 — 공유 조회원만 읽는다', () => {
  const code = strip(HOOK)
  assert.doesNotMatch(code, /fetch\(|discoverPremiumAssets\(/, '훅이 따로 조회한다')
  assert.match(code, /usePremiumAssetsContext\(/)
})

test('신원은 토큰에서 온다 — localStorage user_id 를 서버로 보내지 않는다', () => {
  const code = strip(HOOK) + strip(SECTION) + strip(PROVIDER)
  assert.doesNotMatch(code, /getEternalBeamUserId\(/, 'localStorage 신원을 쓴다')
  assert.match(strip(PROVIDER), /getPremiumAccessToken\(/, '토큰을 쓰지 않는다')
})

test('구독 목업 호출도 토큰을 요구한다', () => {
  const mock = strip(readFileSync('src/lib/subscription-mock.ts', 'utf8'))
  assert.match(mock, /requireToken\(\)/, '인증 없이 구독을 바꿀 수 있다')
  assert.doesNotMatch(mock, /user_id/, '바디로 신원을 보낸다 — 서버가 토큰으로 정해야 한다')
})

test('교체된 크레딧 컴포넌트가 되살아나지 않았다', () => {
  assert.equal(existsSync('src/components/memorial/unlock-features-card.tsx'), false)
  assert.equal(existsSync('src/components/memorial/credits-section.tsx'), false)
})
