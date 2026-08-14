/**
 * 원격 누끼 URL 확보 테스트.
 *
 * 실패 사례: 파이프라인의 dog_only_nobg_url 이 data: URL 이라 백엔드가 가져오지
 * 못했고, COME_CLOSER 제출이 stage="download" 로 떨어졌다. 재누끼 없이 1회
 * 업로드로 해결되는지, 그리고 이미 원격이면 건드리지 않는지 못박는다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  ensureRemoteCutoutUrl,
  isRemoteAssetUrl,
  persistCutoutToStorage,
} from './cutout-remote-asset.ts'

function stubEnv(fetchImpl: typeof fetch) {
  const store = new Map<string, string>()
  ;(globalThis as any).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  }
  ;(globalThis as any).fetch = fetchImpl
  return store
}

const DATA_URL = 'data:image/png;base64,iVBORw0KGgo='
const REMOTE = 'https://cdn.example/anonymous/cid-1/dog_only_nobg.png'

const okUpload = (url: string | null = REMOTE) =>
  (async () => ({ ok: true, status: 200, json: async () => ({ cutout_url: url }) })) as unknown as typeof fetch

// ── 스킴 판정 ───────────────────────────────────────────────────────────────

test('원격 URL 판정은 백엔드 규칙과 같다', () => {
  assert.equal(isRemoteAssetUrl('https://cdn/a.png'), true)
  assert.equal(isRemoteAssetUrl('http://cdn/a.png'), true)
  assert.equal(isRemoteAssetUrl('HTTPS://CDN/A.PNG'), true)
  assert.equal(isRemoteAssetUrl(DATA_URL), false)
  assert.equal(isRemoteAssetUrl('blob:https://app/x'), false)
  assert.equal(isRemoteAssetUrl(''), false)
  assert.equal(isRemoteAssetUrl(null), false)
})

// ── 이미 원격이면 업로드하지 않는다 ─────────────────────────────────────────

test('이미 원격이면 업로드 요청조차 하지 않는다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, status: 200, json: async () => ({ cutout_url: REMOTE }) }
  }) as unknown as typeof fetch)

  const r = await ensureRemoteCutoutUrl(
    { content_id: 'cid-1', dog_only_nobg_url: REMOTE },
    { userId: 'u1' },
  )
  assert.equal(r.url, REMOTE)
  assert.equal(r.pipeline, null, '바뀐 게 없으면 파이프라인을 갱신하지 않는다')
  assert.equal(called, 0, '중복 업로드 금지')
})

// ── data: URL 이면 1회 업로드 후 병합 ───────────────────────────────────────

test('data: URL 이면 업로드하고 파이프라인에 병합한다', async () => {
  const store = stubEnv(okUpload())
  const r = await ensureRemoteCutoutUrl(
    { content_id: 'cid-1', dog_only_nobg_url: DATA_URL },
    { userId: 'u1' },
  )
  assert.equal(r.url, REMOTE)
  assert.equal(r.pipeline?.dog_only_nobg_url, REMOTE)
  const saved = JSON.parse(store.get('eternal_beam_pipeline_v1') as string)
  assert.equal(saved.dog_only_nobg_url, REMOTE, 'sessionStorage 에도 남아야 재시도가 안전하다')
})

test('cutout_display_url 에만 data: 가 있어도 찾아낸다', async () => {
  stubEnv(okUpload())
  const r = await ensureRemoteCutoutUrl(
    { content_id: 'cid-1', dog_only_nobg_url: '', cutout_display_url: DATA_URL },
    { userId: 'u1' },
  )
  assert.equal(r.url, REMOTE)
})

test('content_id 가 없으면 업로드하지 않는다 — 경로를 만들 수 없다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, status: 200, json: async () => ({ cutout_url: REMOTE }) }
  }) as unknown as typeof fetch)
  const r = await ensureRemoteCutoutUrl({ dog_only_nobg_url: DATA_URL }, { userId: 'u1' })
  assert.equal(r.url, null)
  assert.equal(called, 0)
})

test('원격도 data: 도 없으면 null', async () => {
  stubEnv(okUpload())
  const r = await ensureRemoteCutoutUrl({ content_id: 'cid-1' }, { userId: 'u1' })
  assert.equal(r.url, null)
})

// ── 실패는 조용히 null (미리보기를 깨뜨리지 않는다) ─────────────────────────

test('업로드 실패는 null — throw 하지 않는다', async () => {
  stubEnv((async () => ({ ok: false, status: 502, json: async () => ({}) })) as unknown as typeof fetch)
  const r = await ensureRemoteCutoutUrl(
    { content_id: 'cid-1', dog_only_nobg_url: DATA_URL }, { userId: 'u1' },
  )
  assert.equal(r.url, null)
})

test('네트워크 오류도 null', async () => {
  stubEnv((async () => { throw new Error('down') }) as unknown as typeof fetch)
  assert.equal(
    await persistCutoutToStorage({ userId: 'u1', contentId: 'c', dataUrl: DATA_URL }), null)
})

test('서버가 data: URL 을 돌려주면 거부한다 — 원격만 받는다', async () => {
  stubEnv(okUpload(DATA_URL as unknown as string))
  assert.equal(
    await persistCutoutToStorage({ userId: 'u1', contentId: 'c', dataUrl: DATA_URL }), null)
})

test('파이프라인이 없으면 아무것도 하지 않는다', async () => {
  stubEnv(okUpload())
  const r = await ensureRemoteCutoutUrl(null, { userId: 'u1' })
  assert.deepEqual(r, { url: null, pipeline: null })
})
