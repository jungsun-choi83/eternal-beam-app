/**
 * 실제 펫 신원 테스트.
 *
 * 배경 1: idle 과 COME_CLOSER 가 서로 다른 신원을 써서 제출과 조회가 만나지
 * 못했다. 두 경로가 **같은 함수**에서 같은 값을 얻는지 못박는다.
 *
 * 배경 2 (이 파일의 핵심): 처음 구현은 전역 eternal_beam_pet_id 가 있으면 무조건
 * 그것을 썼다. 그래서 사진을 새로 올려 content_id 가 바뀌어도 예전 pet_id 가
 * 남아 조회키를 오염시켰고, 사람이 매번 localStorage 를 지워야 했다.
 * 불변식: 현재 content_id = X → pet_id = pet_X.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  derivePetIdFromContent,
  getEternalBeamPetId,
  setAuthoritativePetId,
} from './pet-identity.ts'

function stubStorage(initial: Record<string, string> = {}) {
  const store = new Map(Object.entries(initial))
  ;(globalThis as any).window = {}
  ;(globalThis as any).localStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  }
  return store
}

const bind = (petId: string, contentId: string) => JSON.stringify({ petId, contentId })

// ── 파생 규칙 ──────────────────────────────────────────────────────────────

test('content_id 에서 파생 — 순수 함수라 제출·조회가 같은 값을 얻는다', () => {
  assert.equal(derivePetIdFromContent('abc123'), 'pet_abc123')
  assert.equal(derivePetIdFromContent('  abc123  '), 'pet_abc123')
})

test('없으면 content_id 에서 파생하고 기록한다', () => {
  const store = stubStorage()
  assert.equal(getEternalBeamPetId('abc123'), 'pet_abc123')
  assert.equal(store.get('eternal_beam_pet_id'), 'pet_abc123', '레거시 리더 호환')
  assert.equal(store.get('eternal_beam_pet_binding'), bind('pet_abc123', 'abc123'))
})

test('인자가 없으면 localStorage 의 content_id 를 쓴다', () => {
  stubStorage({ eternal_beam_content_id: 'cid-77' })
  assert.equal(getEternalBeamPetId(), 'pet_cid-77')
})

test('두 번 불러도 같은 값 — 화면마다 다른 pet_id 가 나오면 안 된다', () => {
  stubStorage({ eternal_beam_content_id: 'cid-5' })
  assert.equal(getEternalBeamPetId(), getEternalBeamPetId())
})

// ── 불변식: 새 업로드가 예전 pet_id 를 상속하지 않는다 ──────────────────────

test('묶이지 않은 잔여 전역 pet_id 는 파생값을 이기지 못한다', () => {
  // 예전 동작: 'stale_pet' 을 그대로 반환 → 새 펫의 자산을 영영 못 찾았다.
  stubStorage({ eternal_beam_pet_id: 'stale_pet' })
  assert.equal(getEternalBeamPetId('newcid'), 'pet_newcid',
    '전역 잔여값이 새 content 의 신원을 덮어쓰면 안 된다')
})

test('다른 content 에 묶인 pet_id 도 이기지 못한다', () => {
  stubStorage({
    eternal_beam_pet_id: 'pet_X',
    eternal_beam_pet_binding: bind('pet_X', 'X'),
  })
  assert.equal(getEternalBeamPetId('Y'), 'pet_Y', 'content 가 X→Y 로 바뀌면 pet 도 바뀐다')
})

test('업로드가 바뀔 때마다 신원이 따라간다 — 수동 정리 불필요', () => {
  const store = stubStorage()
  assert.equal(getEternalBeamPetId('X'), 'pet_X')
  assert.equal(getEternalBeamPetId('Y'), 'pet_Y')
  assert.equal(getEternalBeamPetId('Z'), 'pet_Z')
  assert.equal(store.get('eternal_beam_pet_id'), 'pet_Z', '레거시 키도 따라가야 한다')
})

// ── 권위 있는 값은 현재 content 에 묶였을 때만 존중 ────────────────────────

test('현재 content 에 묶인 권위값은 파생값을 이긴다', () => {
  stubStorage({
    eternal_beam_pet_id: 'server_pet_9',
    eternal_beam_pet_binding: bind('server_pet_9', 'abc123'),
  })
  assert.equal(getEternalBeamPetId('abc123'), 'server_pet_9',
    '크레딧 세션이 준 pet_id 는 같은 content 에서는 존중해야 한다')
})

test('setAuthoritativePetId 가 현재 content 에 묶어 기록한다', () => {
  const store = stubStorage({ eternal_beam_content_id: 'cid-1' })
  setAuthoritativePetId('server_pet_9')
  assert.equal(store.get('eternal_beam_pet_binding'), bind('server_pet_9', 'cid-1'))
  assert.equal(getEternalBeamPetId('cid-1'), 'server_pet_9')
  // 그러나 다음 업로드에는 따라붙지 않는다.
  assert.equal(getEternalBeamPetId('cid-2'), 'pet_cid-2')
})

test('content 를 모른 채 기록하면 결속을 만들지 않는다', () => {
  const store = stubStorage()
  setAuthoritativePetId('server_pet_9')
  assert.equal(store.get('eternal_beam_pet_id'), 'server_pet_9', '레거시 값은 남긴다')
  assert.equal(store.get('eternal_beam_pet_binding'), undefined,
    '어느 content 인지 모르면 묶지 않는다 — 다음 업로드를 오염시키면 안 된다')
})

test('빈 pet_id 는 무시한다', () => {
  const store = stubStorage({ eternal_beam_content_id: 'c' })
  setAuthoritativePetId('   ')
  assert.equal(store.get('eternal_beam_pet_binding'), undefined)
})

// ── 방어 ───────────────────────────────────────────────────────────────────

test('content_id 가 전혀 없으면 레거시 값을 쓰고, 그것도 없으면 null', () => {
  stubStorage({ eternal_beam_pet_id: 'legacy_pet' })
  assert.equal(getEternalBeamPetId(), 'legacy_pet', 'content 를 모르면 유일한 단서다')
  stubStorage()
  assert.equal(getEternalBeamPetId(), null, '신원을 지어내지 않는다')
})

test('손상된 결속 기록은 파생으로 복구된다', () => {
  stubStorage({ eternal_beam_pet_binding: '{not json' })
  assert.equal(getEternalBeamPetId('cid-9'), 'pet_cid-9')
})

// ── 호출부가 갈라지지 않게 ─────────────────────────────────────────────────

test('preview 와 devicePlay 가 같은 신원 함수를 쓴다', async () => {
  const fs = await import('node:fs/promises')
  for (const f of [
    'src/components/memorial/preview-screen.tsx',
    'src/components/memorial/memorial-device-play-screen.tsx',
  ]) {
    const src = await fs.readFile(f, 'utf8')
    assert.match(src, /getEternalBeamPetId\(/, `${f} 가 공용 신원 함수를 쓰지 않는다`)
    assert.doesNotMatch(
      src,
      /localStorage\.getItem\("eternal_beam_pet_id"\)/,
      `${f} 가 pet_id 를 직접 읽는다 — 파생 규칙이 갈라진다`
    )
  }
  // Phase 7I.1: devicePlay 의 프리미엄 발견은 인증 토큰이 신원이다 — 로컬
  // user_id 를 서버로 보내는 경로가 없어야 한다. preview 는 레거시 생성 경로
  // (명시 회귀 스위치)에서만 공용 user_id 를 계속 쓴다.
  const preview = await fs.readFile('src/components/memorial/preview-screen.tsx', 'utf8')
  assert.match(preview, /getEternalBeamUserId\(\)/, 'preview 가 공용 user_id 를 쓰지 않는다')
})

test('권위 있는 쓰기는 모두 결속 함수를 거친다', async () => {
  const fs = await import('node:fs/promises')
  for (const f of ['src/lib/credit-session.ts', 'src/lib/persist-device-content.ts']) {
    const src = await fs.readFile(f, 'utf8')
    assert.doesNotMatch(
      src,
      /localStorage\.setItem\("eternal_beam_pet_id"/,
      `${f} 가 pet_id 를 결속 없이 직접 쓴다 — 다음 업로드를 오염시킨다`
    )
    assert.match(src, /setAuthoritativePetId\(/)
  }
})
