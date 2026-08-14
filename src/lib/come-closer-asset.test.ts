/**
 * COME_CLOSER 자산 → 세션 파이프라인 병합 테스트.
 *
 * 실행: npm test  (node:test + node:assert)
 *
 * 배경: dev 트리거가 생성·승격을 끝내고 GET 으로 URL 을 돌려주는데, 그 값을
 * 파이프라인에 넣는 코드가 없어 preview-screen 이 항상 null 을 봤다.
 * 그래서 더블탭이 아무 일도 하지 않았다. 이 경로를 고정한다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  PIPELINE_STORAGE_KEY,
  fetchComeCloserUrl,
  isComeCloserCacheValid,
  lookupComeCloser,
  mergeComeCloserIntoPipeline,
  resolveComeCloserForPipeline,
  type ComeCloserLookupResult,
} from './come-closer-asset.ts'

/** sessionStorage / fetch 최소 스텁. */
function stubEnv(fetchImpl: typeof fetch) {
  const store = new Map<string, string>()
  ;(globalThis as any).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  }
  ;(globalThis as any).fetch = fetchImpl
  return store
}

const ok = (body: unknown) =>
  (async () => ({ ok: true, status: 200, json: async () => body })) as unknown as typeof fetch
const notFound = (async () => ({ ok: false, status: 404, json: async () => ({}) })) as unknown as typeof fetch
const boom = (async () => {
  throw new Error('network down')
}) as unknown as typeof fetch

const PIPE = {
  content_id: 'c1',
  cutout_display_url: 'u',
  dog_only_nobg_url: 'u',
  idle_video_url: 'https://cdn/breath.mp4',
  action_video_url: '',
}

// ── fetch ──────────────────────────────────────────────────────────────────

test('승격된 URL 을 돌려준다', async () => {
  stubEnv(ok({ come_closer_video_url: 'https://cdn/cc.mp4', ready: true }))
  const url = await fetchComeCloserUrl({ userId: 'u1', placeId: 'snow_forest', petId: 'p1' })
  assert.equal(url, 'https://cdn/cc.mp4')
})

test('아직 승격 전이면 null', async () => {
  stubEnv(ok({ come_closer_video_url: null, ready: false }))
  assert.equal(await fetchComeCloserUrl({ userId: 'u1', placeId: 'snow_forest' }), null)
})

test('dev 트리거가 꺼져 404 면 null (예외 없음)', async () => {
  stubEnv(notFound)
  assert.equal(await fetchComeCloserUrl({ userId: 'u1', placeId: 'snow_forest' }), null)
})

test('네트워크 오류도 null 로 삼킨다 — 미리보기가 깨지면 안 된다', async () => {
  stubEnv(boom)
  assert.equal(await fetchComeCloserUrl({ userId: 'u1', placeId: 'snow_forest' }), null)
})

test('user_id / place_id 가 비면 요청조차 하지 않는다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch)
  assert.equal(await fetchComeCloserUrl({ userId: '  ', placeId: 'snow_forest' }), null)
  assert.equal(await fetchComeCloserUrl({ userId: 'u1', placeId: '' }), null)
  assert.equal(called, 0)
})

// ── 실패 원인 구분 ──────────────────────────────────────────────────────────
// 전부 null 로 뭉개면 "트리거 꺼짐"과 "신원 불일치"와 "생성 전"을 구분할 수 없다.
// 브라우저에서 다음에 할 행동이 셋 다 다르므로 원인은 반드시 살아 있어야 한다.

test('404 → disabled (트리거 꺼짐 / API base 불일치)', async () => {
  stubEnv(notFound)
  const r = await lookupComeCloser({ userId: 'u1', placeId: 'snow_forest' })
  assert.equal(r.reason, 'disabled')
  assert.equal(r.status, 404)
  assert.equal(r.url, null)
})

test('fetch 실패 → network (404 와 구분된다)', async () => {
  stubEnv(boom)
  const r = await lookupComeCloser({ userId: 'u1', placeId: 'snow_forest' })
  assert.equal(r.reason, 'network')
  assert.equal(r.status, undefined, '요청이 성립하지 않았으니 상태 코드가 없어야 한다')
})

test('200 + URL 없음 → not-generated (신원/테마 불일치가 여기 해당)', async () => {
  stubEnv(ok({ come_closer_video_url: null, ready: false }))
  const r = await lookupComeCloser({ userId: 'u1', placeId: 'snow_forest' })
  assert.equal(r.reason, 'not-generated')
  assert.equal(r.status, 200)
})

test('신원이 비면 no-identity — 요청조차 하지 않는다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, json: async () => ({}) }
  }) as unknown as typeof fetch)
  assert.equal((await lookupComeCloser({ userId: ' ', placeId: 'snow_forest' })).reason, 'no-identity')
  assert.equal(called, 0)
})

test('500 → http-error (disabled 로 오인하지 않는다)', async () => {
  stubEnv((async () => ({ ok: false, status: 500, json: async () => ({}) })) as unknown as typeof fetch)
  const r = await lookupComeCloser({ userId: 'u1', placeId: 'snow_forest' })
  assert.equal(r.reason, 'http-error')
  assert.equal(r.status, 500)
})

test('조회에 쓴 키를 그대로 되돌려 준다 — 불일치 진단의 핵심', async () => {
  stubEnv(ok({ come_closer_video_url: null }))
  const r = await lookupComeCloser({ userId: 'cc-test-user', placeId: 'snow_forest', petId: 'cc-test-pet' })
  assert.deepEqual(r.query, { userId: 'cc-test-user', placeId: 'snow_forest', petId: 'cc-test-pet' })
})

test('pet_id 를 비우면 null 로 보고된다 — 백엔드가 `<user>_pet` 으로 조회함을 드러낸다', async () => {
  stubEnv(ok({ come_closer_video_url: null }))
  const r = await lookupComeCloser({ userId: 'cc-test-user', placeId: 'snow_forest' })
  assert.equal(r.query.petId, null)
})

test('resolve 가 onLookup 으로 원인을 전달한다', async () => {
  stubEnv(notFound)
  const seen: ComeCloserLookupResult[] = []
  const out = await resolveComeCloserForPipeline(PIPE as never, {
    userId: 'u1', placeId: 'snow_forest', onLookup: (r) => void seen.push(r),
  })
  assert.equal(out, null, '반환값 의미는 바뀌지 않는다')
  assert.equal(seen.length, 1)
  assert.equal(seen[0].reason, 'disabled')
})

test('이미 URL 이 있으면 onLookup 도 부르지 않는다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, json: async () => ({ come_closer_video_url: 'x' }) }
  }) as unknown as typeof fetch)
  let notified = 0
  await resolveComeCloserForPipeline({ ...PIPE, come_closer_video_url: 'https://cdn/existing.mp4' } as never, {
    userId: 'u1', placeId: 'snow_forest', onLookup: () => void (notified += 1),
  })
  assert.equal(called, 0)
  assert.equal(notified, 0)
})

// ── merge ──────────────────────────────────────────────────────────────────

test('파이프라인에 병합되고 sessionStorage 에 저장된다', () => {
  const store = stubEnv(ok({}))
  const next = mergeComeCloserIntoPipeline(PIPE as never, 'https://cdn/cc.mp4')
  assert.equal(next.come_closer_video_url, 'https://cdn/cc.mp4')
  assert.equal(next.idle_video_url, 'https://cdn/breath.mp4', 'BREATH 는 그대로여야 한다')
  const saved = JSON.parse(store.get('eternal_beam_pipeline_v1') as string)
  assert.equal(saved.come_closer_video_url, 'https://cdn/cc.mp4')
})

test('기존 필드를 지우지 않는다', () => {
  stubEnv(ok({}))
  const next = mergeComeCloserIntoPipeline(PIPE as never, 'https://cdn/cc.mp4')
  for (const k of ['content_id', 'cutout_display_url', 'dog_only_nobg_url', 'idle_video_url']) {
    assert.equal((next as never as Record<string, unknown>)[k], (PIPE as Record<string, unknown>)[k])
  }
})

// ── resolve ────────────────────────────────────────────────────────────────

test('URL 을 찾으면 갱신된 파이프라인을 돌려준다', async () => {
  stubEnv(ok({ come_closer_video_url: 'https://cdn/cc.mp4', ready: true }))
  const next = await resolveComeCloserForPipeline(PIPE as never, {
    userId: 'u1', placeId: 'snow_forest', petId: 'p1',
  })
  assert.ok(next)
  assert.equal(next!.come_closer_video_url, 'https://cdn/cc.mp4')
})

test('이미 URL 이 있으면 재조회하지 않는다', async () => {
  let called = 0
  stubEnv((async () => {
    called += 1
    return { ok: true, json: async () => ({ come_closer_video_url: 'x' }) }
  }) as unknown as typeof fetch)
  const already = { ...PIPE, come_closer_video_url: 'https://cdn/existing.mp4' }
  assert.equal(await resolveComeCloserForPipeline(already as never, {
    userId: 'u1', placeId: 'snow_forest',
  }), null)
  assert.equal(called, 0, '중복 요청 금지')
})

test('파이프라인이 없으면 아무것도 하지 않는다', async () => {
  stubEnv(ok({ come_closer_video_url: 'x' }))
  assert.equal(await resolveComeCloserForPipeline(null, {
    userId: 'u1', placeId: 'snow_forest',
  }), null)
})

test('승격 전이면 파이프라인을 건드리지 않는다', async () => {
  stubEnv(ok({ come_closer_video_url: null, ready: false }))
  assert.equal(await resolveComeCloserForPipeline(PIPE as never, {
    userId: 'u1', placeId: 'snow_forest',
  }), null, 'null 반환 = 기존 BREATH 동작 유지')
})


test('스토리지 키가 ai-processing-screen 과 동기화돼 있다', async () => {
  const fs = await import('node:fs/promises')
  const src = await fs.readFile('src/components/memorial/ai-processing-screen.tsx', 'utf8')
  const m = src.match(/ETERNAL_BEAM_PIPELINE_KEY\s*=\s*"([^"]+)"/)
  assert.ok(m, 'ai-processing-screen 에서 키를 찾지 못했다')
  assert.equal(PIPELINE_STORAGE_KEY, m![1], '두 키가 어긋나면 병합이 조용히 실패한다')
})

// ── 캐시 무효화 (더블탭이 죽는 실제 원인) ──────────────────────────────────
// 예전 가드: `if (pipeline.come_closer_video_url) return`.
// truthy 이기만 하면 조회를 건너뛰어서, 새 사진을 올려 pet 이 바뀌어도 이전 펫의
// URL 이 세션에 남아 조회를 영원히 막았다 → 액션이 안 붙거나 엉뚱한 클립이 붙었다.

test('출처 펫이 같으면 캐시를 신뢰한다', () => {
  const p = { ...PIPE, come_closer_video_url: 'https://cdn/cc.mp4', come_closer_pet_id: 'pet_a' }
  assert.equal(isComeCloserCacheValid(p as never, 'pet_a'), true)
})

test('출처 펫이 다르면 캐시를 버린다 — 새 업로드가 예전 클립을 물려받으면 안 된다', () => {
  const p = { ...PIPE, come_closer_video_url: 'https://cdn/old.mp4', come_closer_pet_id: 'pet_old' }
  assert.equal(isComeCloserCacheValid(p as never, 'pet_new'), false)
})

test('출처 표시가 없는 예전 캐시는 신뢰하지 않는다 (마이그레이션)', () => {
  // 이 필드가 생기기 전에 저장된 세션. 출처를 모르므로 다시 조회해야 한다.
  const p = { ...PIPE, come_closer_video_url: 'https://cdn/legacy.mp4' }
  assert.equal(isComeCloserCacheValid(p as never, 'pet_a'), false)
})

test('URL 이 없으면 당연히 무효', () => {
  assert.equal(isComeCloserCacheValid({ ...PIPE } as never, 'pet_a'), false)
  assert.equal(isComeCloserCacheValid(null, 'pet_a'), false)
})

test('병합이 출처 펫을 함께 기록한다', () => {
  const store = stubEnv(ok({}))
  const next = mergeComeCloserIntoPipeline(PIPE as never, 'https://cdn/cc.mp4', 'pet_a')
  assert.equal(next.come_closer_pet_id, 'pet_a')
  const saved = JSON.parse(store.get('eternal_beam_pipeline_v1') as string)
  assert.equal(saved.come_closer_pet_id, 'pet_a', '세션에도 남아야 새로고침 후에도 판정된다')
  assert.equal(isComeCloserCacheValid(next as never, 'pet_a'), true)
})

test('null 로 비우면 출처도 지운다', () => {
  stubEnv(ok({}))
  const cleared = mergeComeCloserIntoPipeline(
    { ...PIPE, come_closer_video_url: 'x', come_closer_pet_id: 'pet_old' } as never, null)
  assert.equal(cleared.come_closer_video_url, null)
  assert.equal(cleared.come_closer_pet_id, null)
})

test('preview/devicePlay 가 무조건 신뢰하는 옛 가드를 쓰지 않는다', async () => {
  const fs = await import('node:fs/promises')
  for (const f of [
    'src/components/memorial/preview-screen.tsx',
    'src/components/memorial/memorial-device-play-screen.tsx',
  ]) {
    const src = await fs.readFile(f, 'utf8')
    assert.doesNotMatch(
      src, /if \(!pipeline \|\| pipeline\.come_closer_video_url\) return/,
      `${f}: 캐시를 무조건 신뢰하는 옛 가드가 남아 있다`)
    assert.match(src, /isComeCloserCacheValid\(/, `${f}: 출처 검증을 하지 않는다`)
  }
})
