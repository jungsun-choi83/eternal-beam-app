/**
 * Phase 9 — Toss 결제 복귀 경로 배선.
 *
 * Toss 는 결제창을 마치면 **페이지를 이동**시킨다(팝업 아님). 전용 경로가 없으면
 * 앱이 첫 화면(QR 연결)으로 부팅되고, 사용자는 방금 낸 돈의 결과를 볼 수 없다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { billingReturnEntry, readBillingRedirectParams } from './app-entry.ts'

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

function withLocation(pathname: string, search = '') {
  ;(globalThis as Record<string, unknown>).window = { location: { pathname, search } }
}

// ── 경로 감지 ────────────────────────────────────────────────────────────────

test('/billing/success 를 성공 복귀로 인식한다', () => {
  withLocation('/billing/success')
  assert.equal(billingReturnEntry(), 'success')
})

test('/billing/fail 을 실패 복귀로 인식한다', () => {
  withLocation('/billing/fail')
  assert.equal(billingReturnEntry(), 'fail')
})

test('후행 슬래시가 있어도 인식한다', () => {
  withLocation('/billing/success/')
  assert.equal(billingReturnEntry(), 'success')
})

test('그 외 경로는 결제 복귀가 아니다', () => {
  for (const p of ['/', '/forest', '/billing', '/billing/other']) {
    withLocation(p)
    assert.equal(billingReturnEntry(), null, `${p} 를 결제 복귀로 오인했다`)
  }
})

// ── 리다이렉트 파라미터 ──────────────────────────────────────────────────────

test('Toss 가 실어 주는 값을 모두 읽는다', () => {
  const p = readBillingRedirectParams(
    '?authKey=ak_1&customerKey=eb_abc&orderId=eb_initial_1&planId=web_membership'
  )
  assert.deepEqual(p, {
    authKey: 'ak_1', customerKey: 'eb_abc',
    orderId: 'eb_initial_1', planId: 'web_membership',
  })
})

test('값이 없으면 null — 확정을 시도하지 않는다', () => {
  const p = readBillingRedirectParams('')
  assert.equal(p.authKey, null)
  assert.equal(p.orderId, null)
})

// ── 배선 가드 ────────────────────────────────────────────────────────────────

const APP = readFileSync('src/app/EternalBeamApp.tsx', 'utf8')
const SCREEN = readFileSync('src/components/memorial/billing-result-screen.tsx', 'utf8')

test('앱이 결제 복귀를 첫 화면보다 **먼저** 판정한다', () => {
  const code = strip(APP)
  const billing = code.indexOf('billingReturnEntry()')
  const forest = code.indexOf('isPublicForestEntry()')
  assert.ok(billing > 0 && forest > 0)
  assert.ok(billing < forest, '결제 복귀가 다른 진입 판정에 가려진다')
  assert.match(code, /return 'billingResult'/)
})

test('결제 복귀 화면이 렌더된다', () => {
  assert.match(strip(APP), /<BillingResultScreen/)
})

test('확정은 한 번만 시도한다 — 재시도 루프를 만들지 않는다', () => {
  const code = strip(SCREEN)
  assert.match(code, /startedRef/, '중복 확정 가드가 없다')
  assert.match(code, /confirmMembership\(/)
})

test('확정 후 쿼리를 지운다 — 새로고침이 다시 확정을 부르지 않게', () => {
  assert.match(strip(SCREEN), /history\.replaceState/)
})

test('실패 경로는 확정을 시도하지 않는다', () => {
  const code = strip(SCREEN)
  assert.match(code, /if \(outcome === "fail" \|\| startedRef\.current\) return;/)
})

test('결제 화면에 시크릿·billingKey 개념이 없다', () => {
  const code = strip(SCREEN) + strip(readFileSync('src/lib/toss-billing.ts', 'utf8'))
  for (const needle of ['secret_key', 'secretKey', 'billing_key', 'billingKey']) {
    assert.doesNotMatch(code, new RegExp(needle), `${needle} 가 프론트에 있다`)
  }
})

test('SPA 리라이트가 /billing/* 를 index.html 로 보낸다', () => {
  const v = JSON.parse(readFileSync('vercel.json', 'utf8')) as {
    rewrites: { source: string; destination: string }[]
  }
  const spa = v.rewrites.find((r) => r.destination === '/index.html')
  assert.ok(spa, 'SPA 폴백 리라이트가 없다')
  // /billing/success 가 제외 목록(api/outputs/assets/demo)에 걸리지 않아야 한다.
  assert.ok(
    new RegExp(spa!.source).test('/billing/success'),
    '/billing/success 가 index.html 로 가지 않는다'
  )
})

test('갱신 배치 워크플로가 시크릿 없이는 돌지 않는다', () => {
  const wf = readFileSync('.github/workflows/billing-renewal.yml', 'utf8')
  assert.match(wf, /X-Billing-Cron-Secret/, '크론 시크릿 헤더를 보내지 않는다')
  assert.match(wf, /BILLING_CRON_SECRET/)
  assert.match(wf, /workflow_dispatch/, '수동 실행이 없다 — 샌드박스 검증이 불가능하다')
  assert.doesNotMatch(wf, /test_sk_|live_sk_/, '워크플로에 키가 하드코딩됐다')
})
