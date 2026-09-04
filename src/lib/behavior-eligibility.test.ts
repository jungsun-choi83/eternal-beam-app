/**
 * Phase 6 — 런타임 적격성.
 *
 * 최종 규칙 (자발적 · 상호작용 **공통**):
 *
 *     구독 entitled  ∩  자산 READY  ∩  선호 ON
 *
 * 세 조건은 서로 다른 곳에서 독립적으로 변한다(구독 웹훅 / 생성 승격 / 사용자 토글).
 * 규칙이 한 함수에 모여 있지 않으면, 만료된 사용자가 더블탭으로만 프리미엄을 계속
 * 쓰는 식의 구멍이 생긴다.
 *
 * 여기서 지키는 또 하나: **BREATHING 은 이 판정의 대상이 아니다.** 무료 홈 루프는
 * 구독·자산·선호와 무관하게 언제나 돈다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  eligibleBehaviorIds,
  eligibleSources,
  isBehaviorEligible,
  preferenceOf,
} from './behavior-library.ts'
import type { PremiumAssets } from './premium-assets.ts'

const IDLE = ['BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING']
const CC = 'COME_CLOSER'
const ALL = [...IDLE, CC]
const url = (id: string) => `https://cdn.test/${id}.mp4`
const allReady = () => Object.fromEntries(ALL.map((i) => [i, url(i)]))

function assets(over: Partial<PremiumAssets> = {}): PremiumAssets {
  return {
    petId: 'pet1',
    ready: allReady(),
    readyAssets: Object.fromEntries(
      ALL.map((i) => [i, { url: url(i), deliveryFormat: null }])
    ),
    generating: [],
    missing: [],
    idleEvents: IDLE,
    actionEvents: [CC],
    prices: { "idle:BUNDLE": 1, "action:COME_CLOSER": 1 },
    entitled: true,
    preferences: {},
    subscriptionStatus: 'active',
    subscriptionRequired: true,
    ...over,
  }
}

// ── 세 조건이 모두 필요하다 ──────────────────────────────────────────────────

test('ACTIVE + READY + ON → 적격', () => {
  const a = assets()
  for (const id of ALL) assert.equal(isBehaviorEligible(id, a), true, `${id} 가 막혔다`)
})

test('EXPIRED → 전부 부적격 (READY·ON 이어도)', () => {
  const a = assets({ entitled: false, subscriptionStatus: 'expired' })
  for (const id of ALL) {
    assert.equal(isBehaviorEligible(id, a), false, `만료됐는데 ${id} 가 살아 있다`)
  }
})

// ── 크레딧 모드 (Phase 7I.2) — 게이트가 꺼져 있으면 entitled 는 재생을 막지 않는다 ──

test('크레딧 모드: entitled=false 여도 READY+ON 이면 적격 — 소유는 영구다', () => {
  // 크레딧 모드에서 entitled 는 참고값이고 구독 이력 없는 모두에게 false 다.
  // 이것으로 막으면 크레딧을 내고 만든 자산이 영영 재생되지 않는다.
  const a = assets({
    subscriptionRequired: false,
    entitled: false,
    subscriptionStatus: null,
  })
  for (const id of ALL) {
    assert.equal(isBehaviorEligible(id, a), true, `크레딧 모드에서 ${id} 가 막혔다`)
  }
})

test('크레딧 모드에서도 READY/선호 조건은 그대로다', () => {
  const base = { subscriptionRequired: false, entitled: false } as const
  const missing = assets({ ...base, ready: {} })
  assert.equal(isBehaviorEligible('BLINKING', missing), false, 'MISSING 인데 적격이다')
  const off = assets({ ...base, preferences: { BLINKING: false } })
  assert.equal(isBehaviorEligible('BLINKING', off), false, 'OFF 인데 적격이다')
})

test('구서버(subscriptionRequired 필드 없음 → 기본 true)는 보수적으로 구독 게이트를 유지한다', () => {
  // premium-assets.ts 파싱 기본값이 true 다 — 이 조합이 그 계약의 거울이다.
  const a = assets({ subscriptionRequired: true, entitled: false })
  assert.equal(isBehaviorEligible('BLINKING', a), false)
})

test('MISSING → 부적격', () => {
  const a = assets({ ready: {}, missing: [...ALL] })
  for (const id of ALL) assert.equal(isBehaviorEligible(id, a), false)
})

test('GENERATING → 부적격', () => {
  const a = assets({ ready: {}, generating: [...ALL], missing: [] })
  for (const id of ALL) assert.equal(isBehaviorEligible(id, a), false)
})

test('OFF → 부적격 (READY 이고 구독 중이어도)', () => {
  const a = assets({ preferences: Object.fromEntries(ALL.map((i) => [i, false])) })
  for (const id of ALL) assert.equal(isBehaviorEligible(id, a), false, `${id} OFF 가 무시됐다`)
})

test('선호 기본값은 켬 — 저장된 적 없으면 적격', () => {
  const a = assets({ preferences: {} })
  assert.equal(preferenceOf('BLINKING', a), true)
  assert.equal(isBehaviorEligible('BLINKING', a), true)
})

test('세 조건 중 하나만 빠져도 부적격', () => {
  const cases: [string, Partial<PremiumAssets>][] = [
    ['구독 없음', { entitled: false }],
    ['자산 없음', { ready: {}, missing: ['BLINKING'] }],
    ['선호 OFF', { preferences: { BLINKING: false } }],
  ]
  for (const [name, over] of cases) {
    assert.equal(isBehaviorEligible('BLINKING', assets(over)), false, `${name} 인데 적격이다`)
  }
})

test('자산 응답이 없으면 부적격 — 모르면 열지 않는다', () => {
  assert.equal(isBehaviorEligible('BLINKING', null), false)
})

// ── BREATHING 연속성 ─────────────────────────────────────────────────────────

test('BREATHING 은 판정 대상이 아니다 — 후보 필터가 건드리지 않는다', () => {
  // 런타임은 BREATHING 을 idleEventSources/availableIds 로 받지 않는다.
  // 필터에 넣어 봐도 통과하지 못하지만, **애초에 들어가지 않는다**는 것이 계약이다.
  const expired = assets({ entitled: false })
  assert.equal(eligibleBehaviorIds(IDLE, expired).length, 0)
  assert.equal(IDLE.includes('BREATHING'), false, 'BREATHING 이 후보 목록에 있다')
})

test('만료돼도 BREATHING 배선은 손대지 않는다 — 소스 필터 밖이다', () => {
  const play = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')
  // petIdleSrc(=BREATH)는 적격성 필터를 거치지 않고 그대로 넘어간다.
  assert.match(play, /idleVideoUrl=\{petIdleSrc\}/, 'BREATH 소스가 필터를 타고 있다')
})

// ── 자발적 스케줄링 후보 ─────────────────────────────────────────────────────

test('스케줄러 후보는 적격한 것만 남는다', () => {
  const a = assets({
    ready: { BLINKING: url('b'), EAR_TWITCHING: url('e'), HEAD_TILTING: url('h') },
    generating: ['TAIL_WAGGING'],
    missing: [],
    preferences: { EAR_TWITCHING: false },
  })
  assert.deepEqual(eligibleBehaviorIds(IDLE, a), ['BLINKING', 'HEAD_TILTING'])
})

test('적격이 하나도 없으면 후보가 빈다 — 스케줄러는 조용히 멈춘다', () => {
  assert.deepEqual(eligibleBehaviorIds(IDLE, assets({ entitled: false })), [])
})

test('후보 순서를 바꾸지 않는다 — 스케줄러의 선택 규칙을 건드리지 않기 위해', () => {
  assert.deepEqual(eligibleBehaviorIds(IDLE, assets()), IDLE)
})

// ── 소스 표 ──────────────────────────────────────────────────────────────────

test('부적격 행동의 소스는 비워진다 — 수동 트리거도 재생하지 못한다', () => {
  const a = assets({ preferences: { BLINKING: false } })
  const src = eligibleSources({ BLINKING: url('b'), HEAD_TILTING: url('h') }, a)
  assert.equal(src.BLINKING, undefined, 'OFF 인데 소스가 남아 있다')
  assert.equal(src.HEAD_TILTING, url('h'))
})

test('만료되면 소스 표가 통째로 빈다', () => {
  const src = eligibleSources(Object.fromEntries(IDLE.map((i) => [i, url(i)])), assets({ entitled: false }))
  assert.deepEqual(src, {})
})

test('null/빈 URL 은 적격이어도 들어가지 않는다', () => {
  const src = eligibleSources({ BLINKING: null, HEAD_TILTING: '' }, assets())
  assert.deepEqual(src, {})
})

// ── COME_CLOSER 더블탭 게이트 ────────────────────────────────────────────────

test('COME_CLOSER: ACTIVE + READY + ON 이면 허용', () => {
  assert.equal(isBehaviorEligible(CC, assets()), true)
})

test('COME_CLOSER: 만료면 더블탭이 막힌다', () => {
  assert.equal(isBehaviorEligible(CC, assets({ entitled: false })), false)
})

test('COME_CLOSER: OFF 면 더블탭이 막힌다', () => {
  assert.equal(isBehaviorEligible(CC, assets({ preferences: { COME_CLOSER: false } })), false)
})

test('COME_CLOSER: 아직 생성 중이면 막힌다', () => {
  const a = assets({ ready: {}, generating: [CC], missing: [] })
  assert.equal(isBehaviorEligible(CC, a), false)
})

test('COME_CLOSER 와 자발적 행동이 같은 함수를 쓴다', () => {
  // 규칙이 갈라지면 만료 후에도 더블탭만 살아남는 구멍이 생긴다.
  const expired = assets({ entitled: false })
  assert.equal(isBehaviorEligible(CC, expired), isBehaviorEligible('BLINKING', expired))
})

// ── 만료 → 갱신 (재생성 없이 복구) ───────────────────────────────────────────

test('만료는 READY 와 선호를 지우지 않는다 — 적격성만 꺼진다', () => {
  const prefs = { BLINKING: false }
  const active = assets({ preferences: prefs })
  const expired = assets({ preferences: prefs, entitled: false, subscriptionStatus: 'expired' })

  assert.deepEqual(Object.keys(expired.ready).sort(), Object.keys(active.ready).sort())
  assert.equal(preferenceOf('BLINKING', expired), false, '만료가 선호를 되돌렸다')
  assert.equal(preferenceOf('HEAD_TILTING', expired), true)
})

test('갱신하면 예전 READY + ON 이 그대로 다시 적격이 된다', () => {
  const prefs = { EAR_TWITCHING: false }
  const before = eligibleBehaviorIds(IDLE, assets({ preferences: prefs }))
  const expired = eligibleBehaviorIds(IDLE, assets({ preferences: prefs, entitled: false }))
  const renewed = eligibleBehaviorIds(IDLE, assets({ preferences: prefs, entitled: true }))

  assert.deepEqual(expired, [], '만료인데 적격이 남아 있다')
  assert.deepEqual(renewed, before, '갱신 후 목록이 달라졌다')
  assert.deepEqual(renewed, ['BLINKING', 'HEAD_TILTING', 'TAIL_WAGGING'])
})

test('갱신은 생성을 요구하지 않는다 — 자산이 그대로이므로 missing 이 없다', () => {
  const renewed = assets({ entitled: true })
  assert.deepEqual(renewed.missing, [])
})

// ── 배선 가드 ────────────────────────────────────────────────────────────────

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const PLAY = readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8')
const PREVIEW = readFileSync('src/components/memorial/preview-screen.tsx', 'utf8')
const HOOK = readFileSync('src/components/memorial/use-behavior-eligibility.ts', 'utf8')

test('두 화면 모두 스케줄러에 **적격 후보만** 넘긴다', () => {
  for (const [name, src] of [['play', PLAY], ['preview', PREVIEW]] as const) {
    const code = strip(src)
    assert.match(code, /availableIds: eligibleIdleEventIds/, `${name}: 원본 후보를 넘긴다`)
    assert.match(code, /enabled: eligibleIdleEventIds\.length > 0/, `${name}: enabled 가 안 좁혀졌다`)
  }
})

test('두 화면 모두 플레이어에 **적격 소스만** 넘긴다', () => {
  for (const [name, src] of [['play', PLAY], ['preview', PREVIEW]] as const) {
    const code = strip(src)
    assert.match(code, /idleEventSources=\{eligibleIdleEventSources\}/, `${name}: 원본 소스를 넘긴다`)
  }
})

test('두 화면 모두 더블탭을 적격성으로 막는다', () => {
  for (const [name, src] of [['play', PLAY], ['preview', PREVIEW]] as const) {
    const code = strip(src)
    assert.match(code, /if \(comeCloserAllowedRef\.current\)/, `${name}: 더블탭이 무조건 발화한다`)
  }
})

test('적격성은 **새 조회를 만들지 않는다** — 공유 Provider 에서 파생만 한다', () => {
  const code = strip(HOOK)
  assert.match(code, /usePremiumAssetsContext\(/)
  assert.doesNotMatch(code, /fetch\(|discoverPremiumAssets\(|setInterval\(/, '별도 조회·폴링을 만든다')
})

test('스케줄러·런타임 파일은 손대지 않았다', () => {
  // 통합은 **입력을 좁히는 것**뿐이다. 이 파일들에 적격성 개념이 새어 들어가면
  // 타이밍·랜덤·이음매·우선순위 계약이 함께 흔들린다.
  for (const f of [
    'src/components/memorial/use-idle-event-scheduler.ts',
    'src/components/memorial/idle-loop-video.tsx',
    'src/lib/pet-runtime-events.ts',
    'src/lib/idle-event-scheduler.ts',
  ]) {
    const code = strip(readFileSync(f, 'utf8'))
    // 'eligible' 은 제외한다 — 스케줄러가 예전부터 쓰던 자기 어휘다
    // (eligibleIdleEvents = 쿨다운·연속 반복 회피). 프리미엄 개념만 검사한다.
    for (const needle of ['entitled', 'preference', 'subscription', 'membership', 'premium']) {
      assert.doesNotMatch(code, new RegExp(needle, 'i'), `${f} 에 ${needle} 이 새어 들어갔다`)
    }
  }
})

test('두 화면이 공유 Provider 안에서 돈다', () => {
  for (const [name, src] of [['play', PLAY], ['preview', PREVIEW]] as const) {
    const code = strip(src)
    assert.match(code, /<PremiumAssetsProvider/, `${name}: Provider 가 없다`)
    // Provider 는 화면당 하나여야 한다 — 중첩하면 조회가 두 번이다.
    const opens = code.match(/<PremiumAssetsProvider/g) ?? []
    assert.equal(opens.length, 1, `${name}: Provider 가 ${opens.length} 개다`)
  }
})

// ── 엔타이틀먼트 신선도 (Phase 7) ────────────────────────────────────────────

const PROVIDER = readFileSync('src/components/memorial/premium-assets-context.tsx', 'utf8')

test('생성이 없어도 언젠가는 다시 확인한다 — 열어 둔 화면이 만료를 무시하지 않는다', () => {
  const code = strip(PROVIDER)
  assert.match(code, /ENTITLEMENT_REFRESH_MS/, '유휴 상태 재확인 주기가 없다')
  // 타이머는 하나이고 주기만 달라져야 한다 — 두 개면 유휴 탭이 두 배로 조회한다.
  const intervals = code.match(/setInterval\(/g) ?? []
  assert.equal(intervals.length, 1, `setInterval 이 ${intervals.length} 개다`)
  assert.match(code, /generating \? PREMIUM_ASSETS_POLL_MS : ENTITLEMENT_REFRESH_MS/)
})

test('유휴 재확인은 공격적이지 않다 — 분 단위여야 한다', () => {
  // .tsx 는 node --test 가 import 하지 못한다(JSX). 소스에서 상수를 읽는다 —
  // 이 파일의 다른 배선 가드와 같은 방식이다.
  const evalMs = (name: string): number => {
    const m = PROVIDER.match(new RegExp(`export const ${name} = ([^;]+);`))
    assert.ok(m, `${name} 을 찾지 못했다`)
    const v = Function(`"use strict"; return (${m![1]});`)() as number
    assert.equal(typeof v, 'number')
    return v
  }
  const idle = evalMs('ENTITLEMENT_REFRESH_MS')
  const busy = evalMs('PREMIUM_ASSETS_POLL_MS')
  assert.ok(idle >= 60_000, `유휴 폴링이 1분보다 잦다 (${idle}ms)`)
  assert.ok(idle > busy, '유휴 주기가 생성 중 주기보다 짧다')
})

test('탭이 다시 보이면 한 번 확인한다 — 백그라운드 타이머 지연 보완', () => {
  const code = strip(PROVIDER)
  assert.match(code, /visibilitychange/, '가시성 복귀 확인이 없다')
  assert.match(code, /removeEventListener\("visibilitychange"/, '리스너를 정리하지 않는다')
})

test('재확인은 조회일 뿐 — 생성을 일으키지 않는다', () => {
  const code = strip(PROVIDER)
  assert.doesNotMatch(code, /purchasePremium\(/, '재확인이 생성을 제출한다')
  assert.doesNotMatch(code, /method:\s*["']POST["']/)
})
