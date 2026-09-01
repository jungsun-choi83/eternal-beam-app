/**
 * 기본 테마 단일 출처(DEFAULT_THEME_ID / DEFAULT_THEME_KEY) 검증.
 *
 * 이전 상태: 두 기본값이 서로 달랐다.
 *   freeMemorialThemes[0] → fresh_forest (id 8)
 *   getMemorialTheme(1)   → snow_forest  (id 1)
 * 그래서 어느 경로로 폴백하느냐에 따라 미리보기 테마와 COME_CLOSER 조회
 * place_id 가 갈렸다.
 *
 * themes.ts 는 `@/` 별칭 모듈을 import 하지 않는 순수 데이터라 소스에서 직접
 * 파싱해 검증한다(node:test 는 별칭을 해석하지 못한다).
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { resolveSkipThemeId } from './theme-skip.ts'

const THEMES_SRC = readFileSync('src/components/memorial/themes.ts', 'utf8')

/** themes.ts 에서 (id, themeKey, premium) 목록을 배열 순서대로 뽑는다. */
function parseThemes() {
  const out: { id: number; key: string; premium: boolean }[] = []
  for (const m of THEMES_SRC.matchAll(/\{\s*id:\s*(\d+),([\s\S]*?)\n\s{2}\}/g)) {
    const body = m[2]
    const key = /themeKey:\s*"([^"]+)"/.exec(body)
    if (!key) continue
    out.push({
      id: Number(m[1]),
      key: key[1],
      premium: /premium:\s*true/.test(body),
    })
  }
  return out
}

function constNumber(name: string): number {
  const m = new RegExp(`export const ${name}\\s*=\\s*(\\d+)`).exec(THEMES_SRC)
  assert.ok(m, `${name} 를 찾지 못했다`)
  return Number(m![1])
}

const THEMES = parseThemes()
const DEFAULT_THEME_ID = constNumber('DEFAULT_THEME_ID')
const keyOf = (id: number) => THEMES.find((t) => t.id === id)?.key
const DEFAULT_THEME_KEY = keyOf(DEFAULT_THEME_ID)

// ── 단일 출처 ───────────────────────────────────────────────────────────────

test('DEFAULT_THEME_ID 는 실재하는 무료 테마다', () => {
  const t = THEMES.find((x) => x.id === DEFAULT_THEME_ID)
  assert.ok(t, '존재하지 않는 테마를 기본값으로 둘 수 없다')
  assert.equal(t!.premium, false, '기본 테마가 유료면 결제 없이 못 쓴다')
})

test('제품 기본값은 fresh_forest — 기기/데모 동작과 일치', () => {
  assert.equal(DEFAULT_THEME_ID, 8)
  assert.equal(DEFAULT_THEME_KEY, 'fresh_forest')
})

test('DEFAULT_THEME_KEY 는 id 에서 파생된다 (하드코딩 이중화 금지)', () => {
  assert.match(
    THEMES_SRC,
    /DEFAULT_THEME_KEY\s*=\s*\n?\s*memorialThemes\.find\(\(t\) => t\.id === DEFAULT_THEME_ID\)/,
  )
})

test('freeMemorialThemes[0] 과 DEFAULT_THEME_ID 가 이제 일치한다', () => {
  const firstFree = THEMES.filter((t) => !t.premium)[0]
  assert.equal(firstFree.id, DEFAULT_THEME_ID, '두 기본값이 다시 갈라졌다')
})

// ── 모든 폴백 경로가 같은 값을 쓴다 ─────────────────────────────────────────

const FALLBACK_SITES: [string, RegExp][] = [
  ['src/components/memorial/preview-screen.tsx', /getMemorialTheme\(DEFAULT_THEME_ID\)!/],
  ['src/components/memorial/memorial-device-play-screen.tsx', /getMemorialTheme\(DEFAULT_THEME_ID\)!/],
  ['src/lib/credit-pipeline.ts', /theme\?\.themeKey \?\? DEFAULT_THEME_KEY/],
]

for (const [file, pattern] of FALLBACK_SITES) {
  test(`${file.split('/').pop()} 폴백이 단일 출처를 쓴다`, () => {
    assert.match(readFileSync(file, 'utf8'), pattern)
  })
}

test('옛 하드코딩 폴백이 남아 있지 않다', () => {
  for (const [file] of FALLBACK_SITES) {
    const src = readFileSync(file, 'utf8')
    assert.doesNotMatch(src, /getMemorialTheme\(1\)!/, `${file}: getMemorialTheme(1) 폴백 잔존`)
    assert.doesNotMatch(src, /\?\? "snow_forest"/, `${file}: snow_forest 하드코딩 폴백 잔존`)
    assert.doesNotMatch(
      src, /defaultThemeId: freeMemorialThemes\[0\]\?\.id \?\? 1/, `${file}: 옛 기본값 잔존`)
  }
})

test('COME_CLOSER 는 테마에 의존하지 않는다 (배경은 여전히 테마를 쓴다)', () => {
  // 제품 규칙: COME_CLOSER 는 검정 플레이트 위 펫만 생성하므로 배경과 무관하다.
  // 같은 펫이면 어떤 테마에서도 같은 클립 하나를 재사용해야 하고, 따라서 조회에
  // 테마가 섞이면 안 된다(섞이면 테마를 바꿀 때마다 조회가 빗나가고 재생성된다).
  const preview = readFileSync('src/components/memorial/preview-screen.tsx', 'utf8')
  assert.doesNotMatch(
    preview, /placeId: currentTheme\.themeKey/,
    'COME_CLOSER 조회에 테마가 섞였다 — 테마 전환 시 조회 실패/재생성이 생긴다')
  // 배경(미리보기 합성)은 반대로 테마를 그대로 써야 한다.
  assert.match(preview, /getThemeBackgroundApiId\(currentTheme\)/, '미리보기 background_id')

  // 자동 생성 모듈의 키에도 place 가 없어야 한다.
  const autogen = readFileSync('src/lib/come-closer-autogen.ts', 'utf8')
  assert.match(autogen, /comeCloserKey\(userId: string, petId: string \| null\)/,
    '생성 키는 (user_id, pet_id, COME_CLOSER) 여야 한다')
})

// ── 명시적 선택이 항상 이긴다 ───────────────────────────────────────────────

const OPTS = {
  isValidTheme: (id: number) => THEMES.some((t) => t.id === id),
  defaultThemeId: DEFAULT_THEME_ID,
}

test('선택 없음 → 어디서나 하나의 기본값', () => {
  assert.equal(resolveSkipThemeId(null, OPTS), DEFAULT_THEME_ID)
  assert.equal(keyOf(resolveSkipThemeId(null, OPTS)), DEFAULT_THEME_KEY)
})

test('명시적 snow_forest → snow_forest 유지', () => {
  const snow = THEMES.find((t) => t.key === 'snow_forest')!
  assert.equal(resolveSkipThemeId(snow.id, OPTS), snow.id)
  assert.equal(keyOf(resolveSkipThemeId(snow.id, OPTS)), 'snow_forest')
})

test('명시적 fresh_forest → fresh_forest 유지', () => {
  const fresh = THEMES.find((t) => t.key === 'fresh_forest')!
  assert.equal(resolveSkipThemeId(fresh.id, OPTS), fresh.id)
  assert.equal(keyOf(resolveSkipThemeId(fresh.id, OPTS)), 'fresh_forest')
})

test('선택이 있으면 Skip 이 기본값으로 되돌리지 않는다 (모든 테마)', () => {
  for (const t of THEMES) {
    assert.equal(resolveSkipThemeId(t.id, OPTS), t.id, `${t.key} 가 기본값으로 덮였다`)
  }
})

test('기본값과 다른 테마를 골라도 유지된다 (핵심 회귀)', () => {
  const other = THEMES.find((t) => t.id !== DEFAULT_THEME_ID && !t.premium)!
  const result = resolveSkipThemeId(other.id, OPTS)
  assert.equal(result, other.id)
  assert.notEqual(keyOf(result), DEFAULT_THEME_KEY)
})

// ── 의도적인 특정 테마 참조는 건드리지 않았다 ───────────────────────────────

test('forest 데모 상수는 그대로 (기본값이 아니라 특정 체험 화면)', () => {
  const demo = readFileSync('src/lib/forest-demo-config.ts', 'utf8')
  assert.match(demo, /FOREST_THEME_ID = 8/)
  assert.match(demo, /FOREST_THEME_KEY = 'fresh_forest'/)
})

test('기기 forest 데모 트리거도 그대로', () => {
  const bridge = readFileSync('src/lib/pi-sensor-bridge.ts', 'utf8')
  assert.match(bridge, /triggerThemeOnDevice\("fresh_forest"\)/)
})
