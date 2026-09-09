/**
 * 레거시 크레딧 충전(목업) 계약 — **소비자 UI 는 더 이상 없다**.
 *
 * Phase 3 에서 소비자에게 보이는 크레딧 UI 를 Monthly Membership 으로 교체했다.
 * 크레딧 자체는 사라지지 않았다 — 레거시 기기 팩(IDLE/TOUCH/VOICE/NFC)이 계속
 * 쓰고, 그 재원은 이제 멤버십 갱신이 자동으로 채운다(+12/월).
 *
 * 그래서 여기서 지키는 것이 둘로 갈린다:
 *   1. 충전 **라이브러리**는 그대로 정확해야 한다 (레거시 팩이 의존한다).
 *   2. 충전 **UI** 는 소비자 화면에 다시 나타나면 안 된다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'

import { TEST_CREDIT_PACKS } from './credit-packs.ts'

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const TOPUP = readFileSync('src/lib/credit-topup.ts', 'utf8')
const SETTINGS = readFileSync('src/components/memorial/settings-screen.tsx', 'utf8')
const APP = readFileSync('src/app/EternalBeamApp.tsx', 'utf8')
const PLAY = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')

// ── 팩 정의 (레거시 재원) ────────────────────────────────────────────────────

test('요청된 두 팩(2 / 5)이 정의돼 있다', () => {
  assert.deepEqual(
    TEST_CREDIT_PACKS.map((p) => p.credits),
    [2, 5]
  )
})

test('팩 상품 id 는 백엔드 카탈로그와 일치한다', () => {
  const catalog = readFileSync('backend/data/iap_products.py', 'utf8')
  for (const pack of TEST_CREDIT_PACKS) {
    assert.match(
      catalog,
      new RegExp(`"${pack.productId}"`),
      `${pack.productId} 가 백엔드 IAP_PRODUCTS 에 없다`
    )
    assert.match(
      catalog,
      new RegExp(`credits=${pack.credits}`),
      `${pack.productId} 의 크레딧 수가 백엔드와 다르다`
    )
  }
})

// ── 기존 시스템 재사용 ───────────────────────────────────────────────────────

test('두 번째 지갑을 만들지 않는다 — 기존 IAP 경로를 쓴다', () => {
  const code = strip(TOPUP)
  assert.match(code, /verifyAndChargeIAP\(/, '기존 IAP 충전 API 를 쓰지 않는다')
  assert.doesNotMatch(code, /localStorage/, '로컬에 잔액을 따로 저장한다')
  assert.doesNotMatch(code, /balance\s*\+/, '잔액을 프론트에서 계산한다')
})

test('영수증은 매 호출 고유하다 — 같으면 멱등 재전송이라 잔액이 늘지 않는다', () => {
  const code = strip(TOPUP)
  assert.match(code, /Date\.now\(\)/)
  assert.match(code, /Math\.random\(\)/)
})

test('목업이 꺼져 있으면 충전하지 않고 던진다 — 실 결제를 흉내 내지 않는다', () => {
  const code = strip(TOPUP)
  assert.match(code, /if \(!IAP_MOCK_ENABLED\) \{/)
  assert.match(code, /throw new Error/)
})

// ── 소비자 UI 에서 제거됨 ────────────────────────────────────────────────────

test('크레딧 UI 컴포넌트가 삭제됐다', () => {
  assert.equal(
    existsSync('src/components/memorial/credits-section.tsx'),
    false,
    '설정의 크레딧 섹션이 아직 있다'
  )
  assert.equal(
    existsSync('src/components/memorial/unlock-features-card.tsx'),
    false,
    '크레딧 잠금 해제 카드가 아직 있다'
  )
})

test('설정 화면이 크레딧 대신 멤버십을 보여 준다', () => {
  const code = strip(SETTINGS)
  assert.match(code, /<MembershipSection/, '설정에 멤버십 섹션이 없다')
  assert.doesNotMatch(code, /<CreditsSection/, '크레딧 섹션이 되살아났다')
  // 토글 상태 뒤에 숨어 있으면 안 된다 — 상시 노출이어야 한다.
  const line = code.split('\n').find((l) => l.includes('<MembershipSection')) ?? ''
  assert.doesNotMatch(line, /showSubscriptionTest|showIdleTest/, '토글 뒤에 숨어 있다')
})

test('재생 화면이 크레딧 카드 대신 멤버십 카드를 띄운다', () => {
  const code = strip(PLAY)
  assert.match(code, /<MembershipCard/)
  assert.doesNotMatch(code, /<UnlockFeaturesCard/)
})

test('앱 라우팅이 크레딧이 아니라 멤버십으로 간다', () => {
  const code = strip(APP)
  assert.match(
    code,
    /onOpenMembership=\{\(\) => openSettings\('devicePlay', \{ focusMembership: true \}\)\}/
  )
  assert.doesNotMatch(code, /focusCredits/, '크레딧 포커스 경로가 남아 있다')
})

test('소비자 문구에서 크레딧 개념이 사라졌다', () => {
  const i18n = readFileSync('src/components/memorial/memorial-i18n.ts', 'utf8')
  assert.doesNotMatch(i18n, /getCredits:/, '"크레딧 받기" 문구가 남아 있다')
  assert.match(i18n, /membership: \{/, '멤버십 문구 그룹이 없다')

  // 구독 **테스트 패널**(개발 전용, SUBSCRIPTION_MOCK 뒤)은 예외다 — 거기서는
  // 레거시 지갑 잔액을 QA 가 확인해야 한다. 그 블록만 빼고 검사한다.
  const consumer = i18n.replace(/subscriptionTest: \{[\s\S]*?\n {4}\},/g, '')
  assert.doesNotMatch(consumer, /크레딧/, '소비자 문구에 크레딧이 남아 있다')
  // 복수형만 본다 — "Credit or Debit Card" 는 결제 수단 이름이지 재화가 아니다.
  assert.doesNotMatch(consumer, /\bcredits\b/i, '소비자 영문 문구에 credits 가 남아 있다')

  // 위 제외가 실제로 무언가를 지웠는지 확인한다 — 정규식이 빗나가면 이 테스트가
  // 조용히 통과해 버린다.
  assert.ok(consumer.length < i18n.length, 'subscriptionTest 블록 제외가 동작하지 않았다')
})

// ── 레이아웃 회귀: 카드가 재생 영역을 눌러선 안 된다 ─────────────────────────

test('BREATHING 프레임은 shrink-0 이다 — 형제가 늘어도 납작해지지 않는다', () => {
  // 프레임 내용물은 전부 absolute inset-0 이라 내재 높이가 0 이다. flex 자식의
  // 기본값(flex-shrink:1)으로 두면, 아래 형제가 늘어나는 순간 0 까지 눌려
  // aspect-[3/4] 의 너비만 남은 가로 막대가 된다(카드 추가 후 실제로 발생).
  const frame = PLAY.split('\n').find(
    (l) => l.includes('theme-preview-frame') && l.includes('aspect-[3/4]')
  )
  assert.ok(frame, '재생 프레임을 찾지 못했다')
  assert.match(frame!, /shrink-0/, '프레임이 축소 가능해 납작해질 수 있다')
  assert.match(frame!, /max-h-\[min\(52vh,360px\)\]/, '기존 크기 상한이 바뀌었다')
})

test('프레임 아래 영역이 스크롤한다 — 공간이 모자라면 여기가 줄어든다', () => {
  assert.match(
    PLAY,
    /flex-1 min-h-0 overflow-y-auto[^"]*flex flex-col items-center/,
    '아래 콘텐츠가 스크롤 컨테이너 안에 있지 않다'
  )
})
