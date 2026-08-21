/**
 * 설정 화면의 '뒤로' 는 **들어온 화면으로** 돌아가야 한다.
 *
 * 원래 버그: 설정의 onBack 이 navigateTo('home') 으로 하드코딩돼 있었다. Memorial 에서
 * 크레딧을 충전하러 설정에 들어갔다 나오면 흐름의 처음으로 튕겨 나갔고, 충전 → 복귀 →
 * 잠금 해제라는 동선이 그대로 끊겼다(상태는 남아 있지만 그 화면으로 돌아갈 길이 없다).
 *
 * jsdom 이 없어 화면 전환을 렌더로 확인할 수 없으므로, 소스에서 불변식을 고정한다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const APP = readFileSync('src/app/EternalBeamApp.tsx', 'utf8')
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '')
const CODE = strip(APP)

test('돌아갈 화면을 기억하는 상태가 있다', () => {
  assert.match(CODE, /const \[settingsBackTarget, setSettingsBackTarget\] = useState<Screen \| null>\(null\)/)
})

test('설정의 뒤로가 home 으로 하드코딩돼 있지 않다', () => {
  // 이 회귀가 버그의 전부였다.
  const i = CODE.indexOf('<SettingsScreen')
  assert.ok(i > 0, 'SettingsScreen 렌더를 찾지 못했다')
  const block = CODE.slice(i, i + 900)
  assert.doesNotMatch(
    block,
    /onBack=\{\(\) => navigateTo\('home'/,
    "설정의 뒤로가 여전히 home 으로 고정돼 있다",
  )
  assert.match(block, /onBack=\{handleSettingsBack\}/)
})

test('뒤로는 기억한 화면으로 가고, 없으면 예전 동작(home)을 유지한다', () => {
  const i = CODE.indexOf('const handleSettingsBack')
  assert.ok(i > 0)
  const body = CODE.slice(i, i + 400)
  assert.match(body, /settingsBackTarget \?\? 'home'/)
  assert.match(body, /navigateTo\(target, 'back'\)/)
  assert.match(body, /setSettingsBackTarget\(null\)/, '복귀 후 대상을 비우지 않는다')
})

test('설정 진입점은 전부 openSettings 를 거친다 — 기억하지 않는 경로가 없어야 한다', () => {
  // navigateTo('settings') 직접 호출이 남아 있으면 그 경로만 조용히 home 으로 돌아간다.
  const direct = [...CODE.matchAll(/navigateTo\('settings'\)/g)]
  const insideHelper = CODE.slice(
    CODE.indexOf('const openSettings'),
    CODE.indexOf('const handleSettingsBack'),
  )
  assert.equal(
    direct.length,
    [...insideHelper.matchAll(/navigateTo\('settings'\)/g)].length,
    'openSettings 를 거치지 않는 설정 진입점이 있다',
  )
})

test('Home 에서 연 설정은 Home 으로 돌아간다', () => {
  assert.match(CODE, /onSettings=\{\(\) => openSettings\('home'\)\}/)
})

test('Memorial 의 멤버십 진입은 devicePlay 로 돌아간다', () => {
  assert.match(
    CODE,
    /onOpenMembership=\{\(\) => openSettings\('devicePlay', \{ focusMembership: true \}\)\}/,
  )
})

test('설정 복귀가 세션·파이프라인을 지우지 않는다', () => {
  // handleReset / handleLogout 만 sessionStorage 를 지운다. 뒤로가기는 절대 아니다.
  const i = CODE.indexOf('const handleSettingsBack')
  const body = CODE.slice(i, i + 400)
  assert.doesNotMatch(body, /sessionStorage/, '뒤로가기가 세션을 지운다')
  assert.doesNotMatch(body, /setCutoutImage|setSelectedTheme|setPreviewSettings|handleReset/,
    '뒤로가기가 펫·테마·위치 상태를 초기화한다')
})

test('멤버십 강조 플래그는 복귀 시 해제된다', () => {
  const i = CODE.indexOf('const handleSettingsBack')
  assert.match(CODE.slice(i, i + 400), /setFocusMembership\(false\)/)
})

test('기기 설정 → 설정 복귀는 그대로 유지된다', () => {
  // 설정 > 기기설정에서 뒤로 → 설정. 이때 settingsBackTarget 이 유지돼야
  // 이어서 누른 뒤로가 원래 화면(devicePlay 등)으로 간다.
  assert.match(CODE, /onBack=\{\(\) => navigateTo\('settings', 'back'\)\}/)
  const i = CODE.indexOf("navigateTo('settings', 'back')")
  const around = CODE.slice(Math.max(0, i - 300), i + 100)
  assert.doesNotMatch(around, /setSettingsBackTarget\(null\)/, '기기설정 복귀가 대상을 지운다')
})
