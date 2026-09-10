/**
 * Behavior Library — 행동별 상태·생성 계약.
 *
 * PM 확정 규칙:
 *   행동 하나당 상태 **하나**만 보인다: MISSING / GENERATING / READY
 *   MISSING 에만 [Generate] 가 있다.
 *   READY 는 다시 만들 수 없다 (재생성 경로가 아예 없어야 한다).
 *   활성 멤버만 생성 컨트롤에 접근한다.
 *   자동 생성은 없다 — 사용자가 누른 것만 만들어진다.
 *
 * 그룹은 서버 레지스트리에서 온다:
 *   SPONTANEOUS = idleEvents (BLINKING/EAR_TWITCHING/HEAD_TILTING/TAIL_WAGGING)
 *   INTERACTIVE = actionEvents (COME_CLOSER)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import {
  canGenerateBehavior,
  canToggleBehavior,
  deriveBehaviorLibrary,
  type BehaviorLibraryState,
} from './behavior-library.ts'
import { behaviorState } from './premium-unlock.ts'
import type { PremiumAssets } from './premium-assets.ts'

const IDLE = ['BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING']
const CC = 'COME_CLOSER'
const url = (id: string) => `https://cdn.test/${id}.mp4`

function assets(over: Partial<PremiumAssets> = {}): PremiumAssets {
  return {
    petId: 'pet1',
    ready: {},
    generating: [],
    missing: [...IDLE, CC],
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

const derive = (a: PremiumAssets | null, entitled = true) =>
  deriveBehaviorLibrary({ assets: a, entitled })

const itemOf = (s: BehaviorLibraryState, id: string) =>
  s.groups.flatMap((g) => g.items).find((i) => i.id === id)!

// ── 그룹 ─────────────────────────────────────────────────────────────────────

test('PM 이 지정한 5종이 두 그룹으로 나뉜다', () => {
  const s = derive(assets())
  const spont = s.groups.find((g) => g.id === 'spontaneous')!
  const inter = s.groups.find((g) => g.id === 'interactive')!
  assert.deepEqual(spont.items.map((i) => i.id), IDLE)
  assert.deepEqual(inter.items.map((i) => i.id), [CC])
  assert.equal(s.totalCount, 5)
})

test('그룹은 서버 레지스트리에서 온다 — 5번째 자발적 모션이 자동으로 나타난다', () => {
  const five = [...IDLE, 'NOSE_WIGGLE']
  const s = derive(assets({ idleEvents: five, missing: [...five, CC] }))
  assert.equal(s.groups.find((g) => g.id === 'spontaneous')!.items.length, 5)
  assert.equal(s.totalCount, 6)
})

test('BREATHING 은 라이브러리에 없다 — 무료 기본 모션이다', () => {
  const s = derive(assets())
  assert.equal(itemOf(s, 'BREATHING'), undefined)
})

test('레거시 4종은 라이브러리에 없다', () => {
  const s = derive(assets())
  for (const legacy of ['IDLE', 'TOUCH', 'VOICE', 'NFC']) {
    assert.equal(itemOf(s, legacy), undefined, `레거시 ${legacy} 가 섞였다`)
  }
})

// ── 행동당 상태 하나 ─────────────────────────────────────────────────────────

test('READY / GENERATING / MISSING 이 정확히 하나씩 배타적으로 나온다', () => {
  const s = derive(
    assets({
      ready: { BLINKING: url('b') },
      generating: ['EAR_TWITCHING'],
      missing: ['HEAD_TILTING', 'TAIL_WAGGING', CC],
    })
  )
  assert.equal(itemOf(s, 'BLINKING').status, 'ready')
  assert.equal(itemOf(s, 'EAR_TWITCHING').status, 'generating')
  assert.equal(itemOf(s, 'HEAD_TILTING').status, 'missing')
})

test('판정은 premium-unlock 의 behaviorState 와 같은 함수다 — 두 곳이 갈리지 않는다', () => {
  const a = assets({ ready: { BLINKING: url('b') }, generating: [CC] })
  const s = derive(a)
  for (const id of [...IDLE, CC]) {
    assert.equal(itemOf(s, id).status, behaviorState(id, a), `${id} 판정이 어긋난다`)
  }
})

test('READY 면 재생 URL 을 들고 있다', () => {
  const s = derive(assets({ ready: { BLINKING: url('b') } }))
  assert.equal(itemOf(s, 'BLINKING').url, url('b'))
  assert.equal(itemOf(s, 'TAIL_WAGGING').url, null)
})

// ── 재생성 금지 ──────────────────────────────────────────────────────────────

test('READY 는 다시 생성할 수 없다', () => {
  const s = derive(assets({ ready: { BLINKING: url('b') } }))
  assert.equal(canGenerateBehavior(itemOf(s, 'BLINKING'), s), false, 'READY 를 재생성하려 한다')
})

test('GENERATING 도 다시 제출할 수 없다 — 중복 제출 방지', () => {
  const s = derive(assets({ generating: ['EAR_TWITCHING'] }))
  assert.equal(canGenerateBehavior(itemOf(s, 'EAR_TWITCHING'), s), false)
})

test('MISSING 만 생성 가능하다', () => {
  const s = derive(assets())
  for (const id of [...IDLE, CC]) {
    assert.equal(canGenerateBehavior(itemOf(s, id), s), true)
  }
})

test('전부 READY 면 만들 것이 없다', () => {
  const ready = Object.fromEntries([...IDLE, CC].map((i) => [i, url(i)]))
  const s = derive(assets({ ready, missing: [] }))
  assert.deepEqual(s.missingIds, [])
  assert.equal(s.readyCount, 5)
  assert.equal(s.anyGenerating, false)
})

// ── 멤버십 게이트 ────────────────────────────────────────────────────────────

test('멤버가 아니면 어떤 행동도 생성할 수 없다', () => {
  const s = derive(assets(), false)
  assert.equal(s.canGenerate, false)
  for (const id of [...IDLE, CC]) {
    assert.equal(canGenerateBehavior(itemOf(s, id), s), false, `${id} 가 비멤버에게 열렸다`)
  }
})

test('멤버가 아니어도 이미 만든 것은 READY 로 남는다', () => {
  const s = derive(assets({ ready: { BLINKING: url('b') } }), false)
  assert.equal(itemOf(s, 'BLINKING').status, 'ready', '만료가 READY 를 지웠다')
  assert.equal(s.readyCount, 1)
})

test('자산이 없으면 빈 목록 — 임의로 5종을 만들어 내지 않는다', () => {
  const s = derive(null)
  assert.equal(s.totalCount, 0)
  assert.deepEqual(s.missingIds, [])
})

// ── 폴링 판정 ────────────────────────────────────────────────────────────────

test('하나라도 생성 중이면 anyGenerating', () => {
  assert.equal(derive(assets({ generating: [CC] })).anyGenerating, true)
  assert.equal(derive(assets()).anyGenerating, false)
})

// ── 소스 수준 가드 ───────────────────────────────────────────────────────────

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const HOOK = readFileSync('src/components/memorial/use-behavior-library.ts', 'utf8')
const UI = readFileSync('src/components/memorial/behavior-library.tsx', 'utf8')
const MODEL = readFileSync('src/lib/behavior-library.ts', 'utf8')

test('마운트만으로 생성되지 않는다 — effect 는 GET 만 한다', () => {
  const code = strip(HOOK)
  const effects = [...code.matchAll(/useEffect\(\(\) => \{([\s\S]*?)\n  \}, \[/g)]
  assert.ok(effects.length >= 1)
  for (const m of effects) {
    assert.doesNotMatch(m[1], /purchasePremium\(/, 'effect 안에서 생성을 제출한다')
    assert.doesNotMatch(m[1], /\bgenerate\(/, 'effect 가 생성을 자동 실행한다')
  }
})

test('생성은 행동 **한 건씩** 요청한다 — 번들을 쓰지 않는다', () => {
  const code = strip(HOOK)
  assert.match(code, /kind: actionKind\(actionId\)/, '단건 kind 를 보내지 않는다')
  assert.doesNotMatch(code, /KIND_IDLE_BUNDLE/, '번들 요청이 남아 있다 — 4종이 함께 생성된다')
})

test('한 번의 클릭이 두 번 제출되지 않는다 (ref 가드)', () => {
  const code = strip(HOOK)
  assert.match(code, /if \(inflightRef\.current \|\| !petId\) return;/)
})

test('생성 전에 재생성 여부를 다시 확인한다', () => {
  const body = strip(HOOK)
  assert.match(body, /if \(!item \|\| !canGenerateBehavior\(item, current\)\) return;/)
})

test('READY 행에 재생성 경로가 없다 — 있는 버튼은 ON/OFF 뿐이다', () => {
  const code = strip(UI)
  const i = code.indexOf('item.status === "ready"')
  assert.ok(i > 0, 'READY 분기를 찾지 못했다')
  const readyBranch = code.slice(i, code.indexOf('item.status === "generating"'))
  assert.doesNotMatch(readyBranch, /onGenerate/, 'READY 에서 생성을 부를 수 있다')
  assert.doesNotMatch(readyBranch, /t\.generate/, 'READY 에 [생성] 라벨이 있다')
  assert.match(readyBranch, /role="switch"/, 'READY 에 ON/OFF 가 없다')
})

test('비멤버에게는 목록을 아예 그리지 않는다', () => {
  assert.match(strip(UI), /if \(!enabled \|\| !state\.canGenerate\) return null;/)
})

// ── ON/OFF 선호 (Phase 5) ────────────────────────────────────────────────────

test('선호는 status 와 별개다 — MISSING 인 행동에도 값이 있다', () => {
  const s = derive(assets({ preferences: { BLINKING: false, TAIL_WAGGING: false } }))
  assert.equal(itemOf(s, 'BLINKING').status, 'missing')
  assert.equal(itemOf(s, 'BLINKING').enabled, false, '선호가 status 에 눌렸다')
})

test('저장된 값이 없으면 켬 — 서버 기본값과 같다', () => {
  const s = derive(assets({ preferences: {} }))
  for (const id of [...IDLE, CC]) assert.equal(itemOf(s, id).enabled, true)
})

test('OFF 로 저장된 행동은 OFF 로 읽힌다', () => {
  const s = derive(assets({ ready: { BLINKING: url('b') }, preferences: { BLINKING: false } }))
  assert.equal(itemOf(s, 'BLINKING').status, 'ready')
  assert.equal(itemOf(s, 'BLINKING').enabled, false)
})

test('ON/OFF 는 READY 인 행동에만 노출된다', () => {
  const s = derive(
    assets({
      ready: { BLINKING: url('b') },
      generating: ['EAR_TWITCHING'],
      missing: ['HEAD_TILTING', 'TAIL_WAGGING', CC],
    })
  )
  assert.equal(canToggleBehavior(itemOf(s, 'BLINKING')), true)
  assert.equal(canToggleBehavior(itemOf(s, 'EAR_TWITCHING')), false, 'GENERATING 에 토글이 열렸다')
  assert.equal(canToggleBehavior(itemOf(s, 'HEAD_TILTING')), false, 'MISSING 에 토글이 열렸다')
})

test('OFF 여도 READY 는 READY 다 — 재생성 대상이 되지 않는다', () => {
  const s = derive(assets({ ready: { BLINKING: url('b') }, preferences: { BLINKING: false } }))
  assert.equal(canGenerateBehavior(itemOf(s, 'BLINKING'), s), false, 'OFF 가 재생성을 열었다')
  assert.equal(s.readyCount, 1)
  assert.deepEqual(s.missingIds.includes('BLINKING'), false)
})

test('토글은 생성을 부르지 않는다', () => {
  const code = strip(HOOK)
  const i = code.indexOf('const toggle = useCallback')
  assert.ok(i > 0, 'toggle 을 찾지 못했다')
  const body = code.slice(i)
  assert.doesNotMatch(body, /purchasePremium\(/, '토글이 생성을 제출한다')
  assert.match(body, /setBehaviorPreference\(/, '선호 저장 API 를 쓰지 않는다')
})

test('토글은 서버가 돌려준 전체 선호로 수렴한다', () => {
  assert.match(strip(HOOK), /applyPreferences\(prefs\)/, '서버 응답을 반영하지 않는다')
})

test('READY 가 아니면 토글을 호출조차 하지 않는다', () => {
  assert.match(strip(HOOK), /if \(!item \|\| !canToggleBehavior\(item\)\) return;/)
})

test('선호는 아직 재생에 연결되지 않았다 — 스케줄러를 건드리지 않는다', () => {
  for (const [name, src] of [['ui', UI], ['hook', HOOK], ['model', MODEL]] as const) {
    const code = strip(src)
    for (const needle of ['useIdleEventScheduler', 'availableIds', 'triggerRef']) {
      assert.doesNotMatch(code, new RegExp(needle), `${name} 이 스케줄러에 닿는다`)
    }
  }
})

test('재생·스케줄러에 손대지 않는다', () => {
  for (const [name, src] of [['ui', UI], ['hook', HOOK], ['model', MODEL]] as const) {
    const code = strip(src)
    for (const needle of ['scheduler', 'seam', 'priority', 'preempt', 'grounding']) {
      assert.doesNotMatch(code, new RegExp(needle, 'i'), `${name} 에 ${needle} 관심사가 새어 들어갔다`)
    }
  }
})

test('크레딧 개념이 없다 — 멤버십 모델이다', () => {
  for (const [name, src] of [['ui', UI], ['hook', HOOK], ['model', MODEL]] as const) {
    const code = strip(src)
    for (const needle of ['credit', 'wallet', 'balance', '크레딧']) {
      assert.doesNotMatch(code, new RegExp(needle, 'i'), `${name} 에 ${needle} 이 남아 있다`)
    }
  }
})

test('판정 로직을 복제하지 않고 premium-unlock 에서 가져온다', () => {
  assert.match(strip(MODEL), /from "\.\/premium-unlock\.ts"/, '판정을 새로 구현했다')
})

test('재생 화면에 라이브러리가 붙어 있다', () => {
  const play = strip(readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8'))
  assert.match(play, /<BehaviorLibrary/)
})

test('멤버십 카드와 라이브러리가 **같은** 공유 조회원 안에 있다', () => {
  // Phase 6 에서 Provider 가 화면 최상단으로 올라갔다(런타임 적격성도 컨텍스트를
  // 봐야 하므로). 이제 Provider 는 본체(Inner)를 통째로 감싸고, 카드·라이브러리는
  // 그 본체 안에 있다 — 텍스트 위치가 아니라 **그 구조**를 검사한다.
  const play = strip(readFileSync('src/components/memorial/memorial-device-play-screen.tsx', 'utf8'))
  const open = play.indexOf('<PremiumAssetsProvider')
  const close = play.indexOf('</PremiumAssetsProvider>')
  assert.ok(open > 0 && close > open, '공유 Provider 로 감싸지 않았다')

  const inside = play.slice(open, close)
  assert.match(inside, /<MemorialDevicePlayScreenInner/, 'Provider 가 본체를 감싸지 않는다')

  // 본체는 하나뿐이고 카드·라이브러리는 그 안에 있다 → 컨텍스트가 도달한다.
  assert.equal((play.match(/function MemorialDevicePlayScreenInner/g) ?? []).length, 1)
  assert.match(play, /<MembershipCard/, '멤버십 카드가 없다')
  assert.match(play, /<BehaviorLibrary/, '라이브러리가 없다')
  // 본체가 따로 export 되면 Provider 없이 렌더될 수 있다.
  assert.doesNotMatch(play, /export function MemorialDevicePlayScreenInner/, '본체가 노출됐다')
})

test('훅들이 각자 조회하지 않는다 — 중복 폴링이 되살아나지 않았다', () => {
  for (const f of [
    'src/components/memorial/use-behavior-library.ts',
    'src/components/memorial/use-membership.ts',
  ]) {
    const code = strip(readFileSync(f, 'utf8'))
    assert.doesNotMatch(code, /discoverPremiumAssets\(/, `${f} 가 따로 조회한다`)
    assert.doesNotMatch(code, /setInterval\(/, `${f} 가 따로 폴링한다`)
  }
})
