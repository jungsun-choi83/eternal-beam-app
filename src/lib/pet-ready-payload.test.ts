/**
 * /demo/pet-ready 본문 조립 테스트 — packed_url 전송 경로.
 *
 * 실행: npm test  (node:test + node:assert, 새 의존성 없음)
 *
 * 핵심 계약: packed_url 은 **추가** 필드다. idle_url 을 대체하지 않는다 —
 * packed 를 모르는 기존 S23 빌드가 계속 video_url/idle_url 로 동작해야 하기 때문.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { buildPetReadyBody } from './pet-ready-payload.ts'

test('packed 없음 → 기존 본문과 완전히 동일', () => {
  const body = buildPetReadyBody({
    contentId: 'c1',
    idleUrl: 'https://x/idle.mp4',
    cutoutUrl: 'https://x/cut.png',
  })
  assert.deepEqual(body, {
    content_id: 'c1',
    idle_url: 'https://x/idle.mp4',
    cutout_url: 'https://x/cut.png',
  })
  assert.equal('packed_url' in body, false)
})

test('packed 있음 → packed_url 이 추가되고 idle_url 은 그대로', () => {
  const body = buildPetReadyBody({
    contentId: 'c1',
    idleUrl: 'https://x/idle.mp4',
    packedUrl: 'https://x/idle_packed.mp4',
  })
  assert.equal(body.packed_url, 'https://x/idle_packed.mp4')
  assert.equal(body.idle_url, 'https://x/idle.mp4', 'idle_url 이 대체되면 구형 클라이언트가 깨진다')
})

test('packed 만 있어도 전송된다', () => {
  const body = buildPetReadyBody({ contentId: 'c1', packedUrl: 'https://x/a_packed.mp4' })
  assert.equal(body.packed_url, 'https://x/a_packed.mp4')
  assert.equal('idle_url' in body, false)
})

test('공백/빈 문자열 packed 는 키를 만들지 않는다', () => {
  for (const v of ['', '   ', null, undefined]) {
    const body = buildPetReadyBody({ contentId: 'c1', idleUrl: 'https://x/i.mp4', packedUrl: v })
    assert.equal('packed_url' in body, false, `packedUrl=${JSON.stringify(v)}`)
  }
})

test('URL 앞뒤 공백은 잘라서 보낸다', () => {
  const body = buildPetReadyBody({ contentId: ' c1 ', packedUrl: '  https://x/p_packed.mp4  ' })
  assert.equal(body.content_id, 'c1')
  assert.equal(body.packed_url, 'https://x/p_packed.mp4')
})

// ── Device D1 — Phase 7 BREATHING 전송 본문 ─────────────────────────────────

const PHASE7 = {
  contentId: 'c1',
  petId: 'pet_c1',
  motionId: 'BREATHING',
  packedUrl: 'https://s/u/breathing_packed.mp4?token=fresh',
  deliveryFormat: 'packed_alpha',
} as const

test('Phase 7: 검증 통과 본문 — 신원·모션·명시 포맷·구형 호환 키가 전부 실린다', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  const r = buildPhase7PetReadyBody({ ...PHASE7 })
  assert.ok(r.ok)
  assert.equal(r.body.content_id, 'c1')
  assert.equal(r.body.pet_id, 'pet_c1')
  assert.equal(r.body.motion_id, 'BREATHING')
  assert.equal(r.body.delivery_format, 'packed_alpha')
  assert.equal(r.body.packed_url, PHASE7.packedUrl)
  // 구형 S23 빌드 호환 — idle_url/video_url 에도 같은 URL.
  assert.equal(r.body.idle_url, PHASE7.packedUrl)
  // 테마는 이 본문에 절대 없다 — /demo/play 와 분리된 메시지다.
  assert.ok(!('theme_id' in r.body))
})

test('Phase 7: pet/content 신원이 어긋나면 거절 (Phase 7B 결정론 규칙)', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  for (const petId of ['pet_other', 'c1', '', 'pet_']) {
    const r = buildPhase7PetReadyBody({ ...PHASE7, petId })
    assert.ok(!r.ok && r.reason === 'identity_mismatch', `petId=${petId}`)
  }
})

test('Phase 7: D1 은 BREATHING 만 — 다른 모션은 거절', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  for (const motionId of ['BLINKING', 'COME_CLOSER', 'TAIL_WAGGING', 'RUN']) {
    const r = buildPhase7PetReadyBody({ ...PHASE7, motionId })
    assert.ok(!r.ok && r.reason === 'unsupported_motion', motionId)
  }
})

test('Phase 7: URL 없음 → 거절 (구형 키만 남는 전송 방지)', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  const r = buildPhase7PetReadyBody({ ...PHASE7, packedUrl: '  ' })
  assert.ok(!r.ok && r.reason === 'missing_url')
})

test('Phase 7: 포맷 미상(null) 이면 delivery_format 키를 만들지 않는다 — 수신측 파일명 폴백', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  const r = buildPhase7PetReadyBody({ ...PHASE7, deliveryFormat: null })
  assert.ok(r.ok)
  assert.ok(!('delivery_format' in r.body))
})
