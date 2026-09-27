/**
 * 재생 소스 판정 — "진짜 자산인가 데모인가 없는가".
 *
 * 이 판정이 필요한 이유: 데모 클립(goya)도, CutoutIdleMotion 의 CSS 애니메이션도
 * 화면에서는 "숨 쉬는 개"로 보인다. 그래서 "BREATHING 이 나온다"는 관찰만으로는
 * 진짜 펫 BREATH 영상이 붙었는지 알 수 없다.
 *
 * 아래에 memorial-device-play-screen 의 COME_CLOSER 소스 배선 회귀 가드도 둔다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  classifyPlaybackSource,
  formatPlaybackSourceReport,
  playbackSourceRows,
} from './playback-source-report.ts'

const MEMORIAL = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')
const PLAYER = readFileSync('src/components/memorial/pet-idle-display.tsx', 'utf8')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')

// ── 판정 ─────────────────────────────────────────────────────────────────────

test('승격된 생성 자산은 real', () => {
  for (const url of [
    'https://xyz.supabase.co/storage/v1/object/public/pets/u/p/BLINKING.mp4',
    'https://cdn.example.com/COME_CLOSER.mp4',
  ]) {
    assert.equal(classifyPlaybackSource(url), 'real', url)
  }
})

test('데모/폴백 mp4 는 fallback — real 로 새어 들어가면 안 된다', () => {
  for (const url of [
    '/demo/goya_idle_packed.mp4',
    'https://device.eternalbeam.com/demo/goya_idle_packed.mp4',
    'https://x/goya_idle.mp4',
  ]) {
    assert.equal(classifyPlaybackSource(url), 'fallback', url)
  }
})

test('빈 값·공백·null·undefined 는 missing', () => {
  for (const url of ['', '   ', null, undefined]) {
    assert.equal(classifyPlaybackSource(url), 'missing', JSON.stringify(url))
  }
})

test('행 생성은 순서를 보존한다 — BREATHING 을 맨 위에 두기 때문', () => {
  const rows = playbackSourceRows([
    ['BREATHING', 'https://x/IDLE.mp4'],
    ['COME_CLOSER', null],
    ['BLINKING', '/demo/goya_idle_packed.mp4'],
  ])
  assert.deepEqual(rows.map((r) => r.id), ['BREATHING', 'COME_CLOSER', 'BLINKING'])
  assert.deepEqual(rows.map((r) => r.kind), ['real', 'missing', 'fallback'])
  assert.equal(rows[1].url, null, 'missing 은 빈 문자열이 아니라 null 로 정규화된다')
})

test('표 출력에 세 판정이 그대로 드러난다', () => {
  const out = formatPlaybackSourceReport(
    playbackSourceRows([
      ['BREATHING', 'https://x/IDLE.mp4'],
      ['COME_CLOSER', null],
      ['BLINKING', '/demo/goya_idle_packed.mp4'],
    ])
  )
  assert.match(out, /BREATHING\s+= real/)
  assert.match(out, /COME_CLOSER\s+= missing\s+—/)
  assert.match(out, /BLINKING\s+= fallback/)
})

// ── COME_CLOSER 소스 배선 회귀 가드 ──────────────────────────────────────────

test('memorial 은 승격 전이면 계속 지켜본다 — 한 번 묻고 포기하면 no-source 로 굳는다', () => {
  // Phase 7I.1: 폴링 주체가 화면에서 PremiumAssetsProvider 로 옮겨 갔다.
  // Provider 는 generating 이 남아 있는 동안 15초 주기로 재조회하고, READY 가
  // 되는 순간 컨텍스트가 갱신돼 아래 effect 가 다시 돈다. 화면은 컨텍스트를
  // 구독하기만 하면 된다 — 자체 폴링 루프(pollComeCloserUntilReady)는 금지다:
  // 사본이 갈라지면 한쪽만 dev 엔드포인트(프로덕션에서 죽는 경로)를 본다.
  const code = strip(MEMORIAL)
  assert.match(code, /usePremiumAssetsContext\(\)/, 'devicePlay 가 발견 컨텍스트를 구독하지 않는다')
  assert.match(code, /premiumAssets\?\.readyAssets\?\.COME_CLOSER/, 'READY 계약을 읽지 않는다')
  assert.doesNotMatch(code, /pollComeCloserUntilReady\(/, '화면 자체 폴링(dev 경로)이 남아 있다')
  assert.doesNotMatch(code, /lookupComeCloserAsset\(/, 'dev 조회가 남아 있다')
})

test('발견된 URL 은 파이프라인에 병합된다 — 안 하면 플레이어가 못 본다', () => {
  const code = strip(MEMORIAL)
  const i = code.indexOf('readyAssets?.COME_CLOSER')
  assert.ok(i > 0)
  const after = code.slice(i, i + 600)
  assert.match(after, /mergeComeCloserIntoPipeline\(pipeline, readyUrl, petId\)/)
})

test('idleEventSources 가 COME_CLOSER 소스를 덮지 않는다 — 전개 순서가 계약이다', () => {
  // COME_CLOSER 가 전개 뒤에 와야 한다. 앞에 오면 idleEventSources 가 덮어써서
  // 더블탭이 조용히 죽는다.
  const code = strip(PLAYER)
  const spread = code.indexOf('...idleEventSources')
  const cc = code.indexOf('COME_CLOSER:')
  assert.ok(spread > 0 && cc > 0, 'eventSources 조립을 찾지 못했다')
  assert.ok(cc > spread, 'COME_CLOSER 가 전개보다 앞에 있어 덮어써질 수 있다')
})

test('memorial 이 COME_CLOSER 소스를 플레이어로 넘긴다 (적격성 게이트 경유)', () => {
  // Phase 6: 소스는 그대로 연결되지만 **적격성을 거친다**. 만료/OFF 면 null 이
  // 되어 런타임이 no-source 로 거절한다 — 런타임을 고치지 않고 막는 방식이다.
  const code = strip(MEMORIAL)
  assert.match(code, /comeCloserVideoUrl=\{comeCloserSource\}/, 'COME_CLOSER 소스가 연결되지 않았다')
  assert.match(
    code,
    /const comeCloserSource = eligibility\.comeCloserAllowed[\s\S]{0,120}come_closer_video_url/,
    '적격성을 거치지 않고 원본을 넘긴다',
  )
})
