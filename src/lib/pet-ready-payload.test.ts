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

test('M5-lite: 기기 프리로드 4종은 허용 — 각 본문이 자기 motion_id 를 싣는다', async () => {
  const { buildPhase7PetReadyBody, DEVICE_PRELOAD_MOTIONS } = await import('./pet-ready-payload.ts')
  assert.deepEqual([...DEVICE_PRELOAD_MOTIONS], ['BREATHING', 'PET_HEAD', 'LOOK_UP', 'COME_CLOSER'])
  for (const motionId of DEVICE_PRELOAD_MOTIONS) {
    const r = buildPhase7PetReadyBody({ ...PHASE7, motionId })
    assert.ok(r.ok, motionId)
    assert.equal(r.body.motion_id, motionId)
  }
})

test('M5-lite: 프리로드 집합 밖 모션은 여전히 거절 (아이들 4종·RUN·HAPPY 포함)', async () => {
  const { buildPhase7PetReadyBody } = await import('./pet-ready-payload.ts')
  for (const motionId of ['BLINKING', 'EAR_TWITCHING', 'HEAD_TILTING', 'TAIL_WAGGING', 'RUN', 'HAPPY', 'LIE_DOWN']) {
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

// ── Device M5-lite Phase 1 — 다중 모션 프리로드 세트 ────────────────────────

const PRELOAD = {
  contentId: 'c1',
  petId: 'pet_c1',
  motions: [
    { motionId: 'BREATHING', packedUrl: 'https://s/b_packed.mp4?t=1', deliveryFormat: 'packed_alpha' },
    { motionId: 'PET_HEAD', packedUrl: 'https://s/ph_packed.mp4?t=2', deliveryFormat: 'packed_alpha' },
    { motionId: 'LOOK_UP', packedUrl: 'https://s/lu_packed.mp4?t=3', deliveryFormat: 'packed_alpha' },
    { motionId: 'COME_CLOSER', packedUrl: 'https://s/cc_packed.mp4?t=4', deliveryFormat: 'packed_alpha' },
  ],
}

test('M5-lite 세트: 4종이 각자 본문으로 나오고 서로의 URL 을 덮지 않는다', async () => {
  const { buildDeviceMotionPreloadBodies } = await import('./pet-ready-payload.ts')
  const out = buildDeviceMotionPreloadBodies(PRELOAD)
  assert.equal(out.length, 4)
  const byId = Object.fromEntries(out.map((e) => [e.motionId, e.body]))
  assert.equal(byId.BREATHING.packed_url, 'https://s/b_packed.mp4?t=1')
  assert.equal(byId.PET_HEAD.packed_url, 'https://s/ph_packed.mp4?t=2')
  assert.equal(byId.LOOK_UP.packed_url, 'https://s/lu_packed.mp4?t=3')
  assert.equal(byId.COME_CLOSER.packed_url, 'https://s/cc_packed.mp4?t=4')
  for (const [id, body] of Object.entries(byId)) {
    assert.equal(body.motion_id, id)
    assert.equal(body.pet_id, 'pet_c1')
    assert.equal(body.content_id, 'c1')
    assert.equal(body.delivery_format, 'packed_alpha')
    // 구형 빌드 호환 키가 모든 본문에 실린다.
    assert.equal(body.idle_url, body.packed_url)
  }
})

test('M5-lite 세트: BREATHING 이 항상 마지막 — 단일 슬롯 구형 빌드의 홈 루프 보존', async () => {
  const { buildDeviceMotionPreloadBodies } = await import('./pet-ready-payload.ts')
  const out = buildDeviceMotionPreloadBodies(PRELOAD)
  assert.equal(out[out.length - 1].motionId, 'BREATHING')
  // 입력 순서를 뒤집어도 마찬가지다.
  const reversed = buildDeviceMotionPreloadBodies({
    ...PRELOAD,
    motions: [...PRELOAD.motions].reverse(),
  })
  assert.equal(reversed[reversed.length - 1].motionId, 'BREATHING')
})

test('M5-lite 세트: URL 없는 모션은 그 항목만 빠진다 — 없는 자산은 없는 채로', async () => {
  const { buildDeviceMotionPreloadBodies } = await import('./pet-ready-payload.ts')
  const out = buildDeviceMotionPreloadBodies({
    ...PRELOAD,
    motions: [
      { motionId: 'BREATHING', packedUrl: 'https://s/b_packed.mp4' },
      { motionId: 'PET_HEAD', packedUrl: null },
      { motionId: 'LOOK_UP', packedUrl: '   ' },
      { motionId: 'COME_CLOSER', packedUrl: 'https://s/cc_packed.mp4' },
    ],
  })
  assert.deepEqual(out.map((e) => e.motionId), ['COME_CLOSER', 'BREATHING'])
})

test('M5-lite 세트: 미허용 모션·신원 불일치는 본문을 만들지 않는다', async () => {
  const { buildDeviceMotionPreloadBodies } = await import('./pet-ready-payload.ts')
  const withUnknown = buildDeviceMotionPreloadBodies({
    ...PRELOAD,
    motions: [...PRELOAD.motions, { motionId: 'HAPPY', packedUrl: 'https://s/h_packed.mp4' }],
  })
  assert.equal(withUnknown.length, 4, 'HAPPY 는 Phase 1 집합 밖이다')
  const badIdentity = buildDeviceMotionPreloadBodies({ ...PRELOAD, petId: 'pet_other' })
  assert.equal(badIdentity.length, 0, '신원 불일치는 세트 전체가 나가면 안 된다')
})

test('M5-lite 세트: 같은 모션 중복 입력은 마지막 항목이 이긴다', async () => {
  const { buildDeviceMotionPreloadBodies } = await import('./pet-ready-payload.ts')
  const out = buildDeviceMotionPreloadBodies({
    ...PRELOAD,
    motions: [
      { motionId: 'PET_HEAD', packedUrl: 'https://s/old_packed.mp4' },
      { motionId: 'PET_HEAD', packedUrl: 'https://s/new_packed.mp4' },
    ],
  })
  assert.equal(out.length, 1)
  assert.equal(out[0].body.packed_url, 'https://s/new_packed.mp4')
})
