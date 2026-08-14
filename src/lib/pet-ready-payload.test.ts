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
