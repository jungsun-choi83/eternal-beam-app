/**
 * Skip 이 사용자의 테마 선택을 지우지 않는지 검증.
 *
 * 재현된 버그: snow_forest 선택 → Skip → fresh_forest 로 덮어써짐.
 * localStorage 까지 덮여서 새로고침해도 되돌아오지 않았고, 미리보기와
 * COME_CLOSER 조회(place_id)가 모두 엉뚱한 테마를 썼다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { resolveSkipThemeId } from './theme-skip.ts'

/** handleThemeSkip 본문만 잘라 낸다 (다음 최상위 선언 전까지). */
function skipBody(src: string): string {
  const i = src.indexOf('const handleThemeSkip')
  assert.ok(i > 0, 'handleThemeSkip 을 찾지 못했다')
  const rest = src.slice(i)
  const end = rest.indexOf('\n  const handle', 1)
  return end > 0 ? rest.slice(0, end) : rest
}

/** themes.ts 의 실제 값 (id, themeKey, premium) — 배열 순서 그대로. */
const THEMES = [
  { id: 8, key: 'fresh_forest', premium: false },
  { id: 1, key: 'snow_forest', premium: false },
  { id: 2, key: 'celestial', premium: false },
  { id: 3, key: 'golden_meadow', premium: false },
  { id: 4, key: 'starlight', premium: false },
  { id: 5, key: 'aurora', premium: true },
]
const FREE = THEMES.filter((t) => !t.premium)
const DEFAULT_FREE_ID = FREE[0].id // = 8 (fresh_forest)

const OPTS = {
  isValidTheme: (id: number) => THEMES.some((t) => t.id === id),
  defaultThemeId: DEFAULT_FREE_ID,
}

const keyOf = (id: number) => THEMES.find((t) => t.id === id)?.key

/** handleThemeSkip 이 하는 일: id 결정 → state 반영 → 영속화. */
function pressSkip(selectedTheme: number | null) {
  const themeId = resolveSkipThemeId(selectedTheme, OPTS)
  const store = new Map<string, string>()
  store.set('eternal_beam_theme_id', String(themeId))
  store.set('eternal_beam_theme_key', keyOf(themeId) ?? '')
  return { selectedThemeAfter: themeId, persisted: store, placeId: keyOf(themeId) }
}

// ── 선택이 있을 때: 유지 ────────────────────────────────────────────────────

test('snow_forest 선택 후 Skip → 선택이 유지된다 (원래 버그)', () => {
  const r = pressSkip(1)
  assert.equal(r.selectedThemeAfter, 1)
  assert.equal(r.placeId, 'snow_forest')
  assert.notEqual(r.placeId, 'fresh_forest', 'fresh_forest 로 덮어쓰면 안 된다')
})

test('persisted 테마가 최종 선택과 일치한다', () => {
  const r = pressSkip(1)
  assert.equal(r.persisted.get('eternal_beam_theme_id'), '1')
  assert.equal(r.persisted.get('eternal_beam_theme_key'), 'snow_forest')
})

test('COME_CLOSER 조회 place_id 가 선택한 테마다', () => {
  assert.equal(pressSkip(1).placeId, 'snow_forest')
  assert.equal(pressSkip(4).placeId, 'starlight')
})

for (const t of THEMES) {
  test(`${t.key} 선택 후 Skip → ${t.key} 유지`, () => {
    assert.equal(resolveSkipThemeId(t.id, OPTS), t.id)
  })
}

test('프리미엄 테마 선택도 Skip 이 지우지 않는다 (라우팅은 상위에서 판단)', () => {
  assert.equal(resolveSkipThemeId(5, OPTS), 5)
})

// ── 선택이 없을 때: 기본 무료 테마 ──────────────────────────────────────────

test('선택 없이 Skip → 기본 무료 테마(fresh_forest)', () => {
  const r = pressSkip(null)
  assert.equal(r.selectedThemeAfter, DEFAULT_FREE_ID)
  assert.equal(r.placeId, 'fresh_forest')
})

test('undefined 도 기본값으로 떨어진다', () => {
  assert.equal(resolveSkipThemeId(undefined, OPTS), DEFAULT_FREE_ID)
})

test('알 수 없는 테마 id 는 기본값으로 (손상된 저장값 방어)', () => {
  assert.equal(resolveSkipThemeId(999, OPTS), DEFAULT_FREE_ID)
})

test('무료 목록이 비어도 안전한 폴백', () => {
  assert.equal(
    resolveSkipThemeId(null, { isValidTheme: () => false, defaultThemeId: 1 }),
    1,
  )
})

// ── 회귀 가드 ───────────────────────────────────────────────────────────────

test('freeMemorialThemes[0] 은 fresh_forest, id 1 은 snow_forest — 두 기본값이 다르다', () => {
  assert.equal(DEFAULT_FREE_ID, 8, 'themes.ts 배열 첫 무료 테마')
  assert.equal(keyOf(8), 'fresh_forest')
  assert.equal(keyOf(1), 'snow_forest', 'preview 폴백 getMemorialTheme(1) 은 snow_forest 다')
})

test('EternalBeamApp 이 더 이상 무조건 덮어쓰지 않는다', async () => {
  const fs = await import('node:fs/promises')
  const src = await fs.readFile('src/app/EternalBeamApp.tsx', 'utf8')
  const body = skipBody(src)
  assert.ok(body.includes('resolveSkipThemeId'), 'Skip 은 헬퍼를 써야 한다')
  assert.doesNotMatch(
    body,
    /const themeId = freeMemorialThemes\[0\]\?\.id \?\? 1/,
    '무조건 기본값으로 덮어쓰는 옛 코드가 돌아왔다',
  )
})

test('Skip 의 나머지 라우팅 로직은 보존돼 있다', async () => {
  const fs = await import('node:fs/promises')
  const src = await fs.readFile('src/app/EternalBeamApp.tsx', 'utf8')
  const body = skipBody(src)
  for (const needle of [
    'persistThemeChoice(themeId)',
    'scheduleThemeBackgroundSync(themeId)',
    'isForestTheme(themeId)',
    'canEnterDevicePlay',
    "navigateTo('devicePlay')",
    "navigateTo('preview')",
  ]) {
    assert.ok(body.includes(needle), `라우팅 로직이 사라졌다: ${needle}`)
  }
})
