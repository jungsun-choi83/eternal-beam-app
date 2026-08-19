/**
 * 크레딧 충전(목업) 배선 계약.
 *
 * 원래 문제: Memorial 이 "설정에서 크레딧을 충전하세요" 라고 안내하는데, 설정에는
 * 충전할 수 있는 곳이 없었다(잔액조차 숨은 테스트 패널 안에만 있었다).
 *
 * 여기서 지키는 것:
 *   * 두 번째 지갑을 만들지 않는다 — 기존 IAP 경로를 그대로 쓴다.
 *   * 영수증은 매번 고유하다 (같으면 백엔드가 멱등 재전송으로 처리해 잔액이 안 는다).
 *   * 잔액은 **서버 응답**으로 갱신한다 (로컬 덧셈 금지).
 *   * Memorial 은 잔액 부족일 때 잠금 해제를 비활성으로 두고 충전 경로를 연다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { TEST_CREDIT_PACKS } from './credit-packs.ts'

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const TOPUP = readFileSync('src/lib/credit-topup.ts', 'utf8')
const SECTION = readFileSync('src/components/memorial/credits-section.tsx', 'utf8')
const SETTINGS = readFileSync('src/components/memorial/settings-screen.tsx', 'utf8')
const CARD = readFileSync('src/components/memorial/unlock-features-card.tsx', 'utf8')
const APP = readFileSync('src/app/EternalBeamApp.tsx', 'utf8')

// ── 팩 정의 ──────────────────────────────────────────────────────────────────

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

test('2 크레딧 팩이 잠금 해제 총액(2)을 정확히 채운다', () => {
  // 확정 모델: IDLE_BUNDLE 1 + COME_CLOSER 1 = 2.
  assert.equal(TEST_CREDIT_PACKS[0].credits, 2)
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

// ── 설정 화면 ────────────────────────────────────────────────────────────────

test('크레딧 섹션이 설정에 **상시 노출**된다 — 숨은 패널 뒤가 아니다', () => {
  const code = strip(SETTINGS)
  assert.match(code, /<CreditsSection/, '설정에 크레딧 섹션이 없다')
  // 토글 상태(showSubscriptionTest 등)에 걸려 있으면 안 된다.
  const line = code.split('\n').find((l) => l.includes('<CreditsSection')) ?? ''
  assert.doesNotMatch(line, /showSubscriptionTest|showIdleTest/, '토글 뒤에 숨어 있다')
})

test('잔액은 서버 응답으로 갱신한다', () => {
  const code = strip(SECTION)
  assert.match(code, /setBalance\(r\.creditsRemaining\)/, '로컬에서 잔액을 더하고 있다')
  assert.match(code, /getWalletBalance\(/, '지갑 조회를 하지 않는다')
})

test('충전 중 중복 클릭이 막힌다', () => {
  const code = strip(SECTION)
  assert.match(code, /if \(busy\) return;/)
  assert.match(code, /disabled=\{busy != null\}/)
})

// ── Memorial → 설정 크레딧 ───────────────────────────────────────────────────

test('잔액 부족이면 잠금 해제는 비활성, 크레딧 받기는 활성', () => {
  const code = strip(CARD)
  assert.match(code, /disabled=\{purchasing \|\| insufficient\}/, '잠금 해제가 비활성되지 않는다')
  assert.match(code, /insufficient && onGetCredits/, '크레딧 받기 버튼이 없다')
  // "크레딧 받기" 버튼 자체에는 disabled 가 없어야 한다.
  const i = code.indexOf('onClick={onGetCredits}')
  assert.ok(i > 0)
  const btn = code.slice(Math.max(0, i - 200), i + 200)
  assert.doesNotMatch(btn, /disabled=/, '크레딧 받기가 비활성이면 막다른 길이 된다')
})

test('Memorial 의 크레딧 받기가 설정으로 이동하며 섹션을 강조한다', () => {
  // 진입은 openSettings 를 거친다(돌아갈 화면을 함께 기억한다 —
  // settings-return-nav.test.ts 가 복귀 계약을 따로 고정한다).
  const code = strip(APP)
  assert.match(code, /onGetCredits=\{\(\) => openSettings\('devicePlay', \{ focusCredits: true \}\)\}/)
  assert.match(code, /setFocusCredits\(true\)/, '크레딧 섹션 강조 신호가 없다')
  assert.match(code, /focusCredits=\{focusCredits\}/, '설정에 포커스 신호를 넘기지 않는다')
})

test('크레딧 섹션이 포커스 신호를 받으면 스크롤·강조한다', () => {
  const code = strip(SECTION)
  assert.match(code, /focusOnMount/)
  assert.match(code, /scrollIntoView/)
})

test('안내 문구가 실제로 존재하는 곳을 가리킨다', () => {
  // 설정에 크레딧 컨트롤이 없는데 "설정에서 충전하세요" 라고 하면 안 된다.
  // 지금은 컨트롤이 있으므로 안내 + Get Credits 버튼이 함께 있어야 한다.
  const i18n = readFileSync('src/components/memorial/memorial-i18n.ts', 'utf8')
  assert.match(i18n, /getCredits: "Get Credits"/)
  assert.match(i18n, /credits: \{/)
})

// ── 레이아웃 회귀: 잠금 카드가 재생 영역을 눌러선 안 된다 ────────────────────

test('BREATHING 프레임은 shrink-0 이다 — 형제가 늘어도 납작해지지 않는다', () => {
  // 프레임 내용물은 전부 absolute inset-0 이라 내재 높이가 0 이다. flex 자식의
  // 기본값(flex-shrink:1)으로 두면, 아래 형제가 늘어나는 순간 0 까지 눌려
  // aspect-[3/4] 의 너비만 남은 가로 막대가 된다(잠금 카드 추가 후 실제로 발생).
  const memorial = readFileSync(
    'src/components/memorial/memorial-device-play-screen.tsx',
    'utf8'
  )
  const frame = memorial
    .split('\n')
    .find((l) => l.includes('theme-preview-frame') && l.includes('aspect-[3/4]'))
  assert.ok(frame, '재생 프레임을 찾지 못했다')
  assert.match(frame!, /shrink-0/, '프레임이 축소 가능해 납작해질 수 있다')
  assert.match(frame!, /max-h-\[min\(52vh,360px\)\]/, '기존 크기 상한이 바뀌었다')
})

test('프레임 아래 영역이 스크롤한다 — 공간이 모자라면 여기가 줄어든다', () => {
  const memorial = readFileSync(
    'src/components/memorial/memorial-device-play-screen.tsx',
    'utf8'
  )
  assert.match(
    memorial,
    /flex-1 min-h-0 overflow-y-auto[^"]*flex flex-col items-center/,
    '아래 콘텐츠가 스크롤 컨테이너 안에 있지 않다'
  )
})
