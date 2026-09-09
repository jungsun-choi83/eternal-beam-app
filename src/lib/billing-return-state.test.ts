/**
 * 결제 왕복 후 **같은 펫으로 복귀**한다.
 *
 * 회귀 대상(실제로 났던 버그): Toss 결제가 성공하면 페이지가 리다이렉트되어
 * React state 가 통째로 사라지고, 앱이 업로드 화면부터 다시 시작했다. 사용자는
 * 방금 결제한 뒤 자기 펫 대신 "사진을 올리세요"를 봤다.
 *
 * 여기서 고정하는 계약:
 *   Memorial(devicePlay) → Toss → 성공 → **같은 펫의 Memorial**
 *   실패·취소도 같은 화면으로 돌아온다
 *   펫이 바뀌었거나 BREATHING 이 없으면 복원하지 않는다 (틀린 펫을 보여주느니 설정으로)
 *   복원은 **기존 자산을 가리키기만 한다** — 새 펫을 만들거나 생성을 다시 시작하지 않는다
 */

import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  BILLING_RETURN_KEY,
  clearBillingReturnState,
  readBillingReturnState,
  resolveBillingReturn,
  saveBillingReturnState,
  type BillingReturnState,
} from './billing-return-state.ts'

// node 에는 sessionStorage 가 없다 — 최소 구현으로 대체한다.
const store = new Map<string, string>()
;(globalThis as Record<string, unknown>).sessionStorage = {
  getItem: (k: string) => store.get(k) ?? null,
  setItem: (k: string, v: string) => void store.set(k, v),
  removeItem: (k: string) => void store.delete(k),
}

const SETTINGS = { scale: 1.4, posX: 12, posY: -8 }
const PET = 'content_abc123'
const snapshot = (over: Partial<BillingReturnState> = {}): BillingReturnState => ({
  screen: 'devicePlay',
  settings: SETTINGS,
  contentId: PET,
  ...over,
})
const pipeline = (over: Record<string, unknown> = {}) => ({
  content_id: PET,
  idle_video_url: 'https://cdn.test/breathing.mp4',
  ...over,
})

beforeEach(() => store.clear())

// ── 핵심 회귀: Memorial → Toss → 성공 → 같은 펫 Memorial ─────────────────────

test('Memorial → Toss → 성공 → **같은 펫의 Memorial** 로 돌아온다', () => {
  // 1) Memorial 에서 결제를 시작한다 — 앱이 스냅샷을 남긴다.
  saveBillingReturnState(snapshot())

  // 2) Toss 가 리다이렉트한다 → 새 문서. React state 는 사라졌지만
  //    sessionStorage 의 파이프라인과 스냅샷은 살아 있다.
  const restored = resolveBillingReturn(readBillingReturnState(), pipeline())

  // 3) 업로드가 아니라 Memorial 로, 같은 위치 그대로.
  assert.ok(restored, '결제 후 복원되지 않았다 — 업로드 화면으로 떨어진다')
  assert.equal(restored!.screen, 'devicePlay')
  assert.deepEqual(restored!.settings, SETTINGS, '펫 위치가 초기화됐다')
})

test('복원은 기존 펫을 가리킬 뿐 — 새 펫을 만들지 않는다', () => {
  // 스냅샷에 누끼·테마·파이프라인이 들어 있으면 사본이 갈라지고, 최악의 경우
  // 복원이 "새 펫 만들기"처럼 동작한다. 저장 대상은 화면·위치·지문뿐이어야 한다.
  saveBillingReturnState(snapshot())
  const raw = JSON.parse(store.get(BILLING_RETURN_KEY)!) as Record<string, unknown>
  assert.deepEqual(Object.keys(raw).sort(), ['contentId', 'screen', 'settings'])
  for (const forbidden of ['cutout', 'pipeline', 'theme', 'idle_video_url', 'dog_only']) {
    assert.doesNotMatch(
      JSON.stringify(raw),
      new RegExp(forbidden, 'i'),
      `스냅샷에 ${forbidden} 가 들어갔다 — 자산 사본이 갈라진다`
    )
  }
})

test('preview 화면에서 결제해도 preview 로 돌아온다', () => {
  saveBillingReturnState(snapshot({ screen: 'preview' }))
  assert.equal(resolveBillingReturn(readBillingReturnState(), pipeline())!.screen, 'preview')
})

// ── 복원하면 안 되는 경우 ────────────────────────────────────────────────────

test('스냅샷이 없으면 복원하지 않는다 — 설정 화면으로 간다', () => {
  assert.equal(resolveBillingReturn(readBillingReturnState(), pipeline()), null)
})

test('펫이 바뀌었으면 복원하지 않는다 — 틀린 펫을 보여주지 않는다', () => {
  saveBillingReturnState(snapshot())
  const other = pipeline({ content_id: 'content_DIFFERENT' })
  assert.equal(resolveBillingReturn(readBillingReturnState(), other), null)
})

test('BREATHING 이 없으면 복원하지 않는다 — 빈 재생 화면이 된다', () => {
  saveBillingReturnState(snapshot())
  assert.equal(resolveBillingReturn(readBillingReturnState(), pipeline({ idle_video_url: '' })), null)
  assert.equal(resolveBillingReturn(readBillingReturnState(), pipeline({ idle_video_url: null })), null)
})

test('파이프라인이 아예 없으면 복원하지 않는다', () => {
  saveBillingReturnState(snapshot())
  assert.equal(resolveBillingReturn(readBillingReturnState(), null), null)
})

test('손상된 스냅샷은 조용히 무시한다', () => {
  store.set(BILLING_RETURN_KEY, '{ not json')
  assert.equal(readBillingReturnState(), null)
  store.set(BILLING_RETURN_KEY, JSON.stringify({ screen: 'home', settings: SETTINGS }))
  assert.equal(readBillingReturnState(), null, '복원 대상이 아닌 화면을 받아들였다')
})

test('스냅샷에 지문이 없으면 파이프라인만 보고 복원한다 (구버전 호환)', () => {
  saveBillingReturnState(snapshot({ contentId: null }))
  assert.ok(resolveBillingReturn(readBillingReturnState(), pipeline()))
})

test('복원 후 스냅샷을 지운다 — 다음 새로고침이 다시 튀지 않게', () => {
  saveBillingReturnState(snapshot())
  clearBillingReturnState()
  assert.equal(readBillingReturnState(), null)
})

// ── 앱 배선 가드 ─────────────────────────────────────────────────────────────

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const APP = strip(readFileSync('src/app/EternalBeamApp.tsx', 'utf8'))

test('재생 화면에 있는 동안 스냅샷을 남긴다', () => {
  assert.match(APP, /saveBillingReturnState\(\{/, '스냅샷을 저장하지 않는다')
  assert.match(
    APP,
    /if \(screen !== 'devicePlay' && screen !== 'preview'\) return/,
    '복원 가능한 화면에서만 저장해야 한다'
  )
  assert.match(APP, /\}, \[screen, previewSettings\]\)/, '위치 변경이 스냅샷에 반영되지 않는다')
})

test('성공·실패·취소가 **같은** 복귀 처리를 쓴다', () => {
  // 두 경로가 갈라지면 취소했을 때만 업로드 화면으로 떨어지는 버그가 남는다.
  assert.match(APP, /onContinue=\{handleBillingReturn\}/)
  const i = APP.indexOf('const handleBillingReturn')
  assert.ok(i > 0, '복귀 핸들러가 없다')
  const body = APP.slice(i, i + 900)
  assert.match(body, /resolveBillingReturn\(/)
  assert.match(body, /clearBillingReturnState\(\)/)
  assert.match(body, /navigateTo\(restored\.screen\)/, '복원한 화면으로 가지 않는다')
  assert.match(body, /openSettings\('home'/, '복원 실패 시 안전 경로가 없다')
})

test('복원이 생성·업로드를 다시 시작하지 않는다', () => {
  const i = APP.indexOf('const handleBillingReturn')
  const body = APP.slice(i, i + 900)
  for (const forbidden of [
    'setCutoutImage', 'setUploadedImage', 'handleReset',
    'finalizePreviewContent', 'generate', 'purchasePremium',
  ]) {
    assert.doesNotMatch(
      body,
      new RegExp(forbidden),
      `복귀 처리가 ${forbidden} 를 호출한다 — 펫/생성이 다시 시작된다`
    )
  }
})

test('복귀가 세션 파이프라인을 지우지 않는다 — 펫이 살아 있어야 한다', () => {
  const i = APP.indexOf('const handleBillingReturn')
  const body = APP.slice(i, i + 900)
  assert.doesNotMatch(body, /removeItem\(ETERNAL_BEAM_PIPELINE_KEY\)/)
  assert.doesNotMatch(body, /sessionStorage\.clear/)
})
