/**
 * COME_CLOSER 자동 생성 시나리오 A~I.
 *
 * 핵심 계약: **정확히 1회**. 클라이언트 가드는 왕복을 줄이는 용도이고,
 * 최종 권위는 서버(dev_premium 의 canonical/진행중 검사)다 —
 * 그쪽은 backend/tests/test_come_closer_autogen_idempotency.py 에서 검증한다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  isComeCloserCacheValid,
  mergeComeCloserIntoPipeline,
} from './come-closer-asset.ts'
import {
  __resetAutogenGuards,
  comeCloserKey,
  ensureComeCloser,
  type ComeCloserState,
  MAX_SUBMIT_ATTEMPTS,
} from './come-closer-autogen.ts'

const REMOTE = 'https://cdn.example/u1/cid-1/dog_only_nobg.png'
const CANON = 'https://cdn.example/u1/pet_cid-1/SNOW_FOREST_COME_CLOSER.mp4'
const DATA_URL = 'data:image/png;base64,iVBORw0KGgo='

type Call = { url: string; method: string; body: any }

/**
 * 백엔드 스텁. `canonical` 이 있으면 GET 이 URL 을 돌려주고, POST 는 서버
 * 멱등성을 흉내 낸다(canonical 있으면 ready, 진행 중이면 generated=false).
 */
function stubBackend(opts: {
  canonical?: string | null
  activeJob?: boolean
  postStatus?: number
  /** POST 응답 본문을 고정한다 (예: { status: 'queued' }). */
  postBody?: Record<string, unknown>
  uploadUrl?: string | null
} = {}) {
  const calls: Call[] = []
  let canonical = opts.canonical ?? null
  let active = opts.activeJob ?? false

  const store = new Map<string, string>()
  ;(globalThis as any).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  }
  ;(globalThis as any).fetch = (async (url: string, init: any = {}) => {
    const method = init.method || 'GET'
    const body = init.body ? JSON.parse(init.body) : null
    calls.push({ url, method, body })

    if (url.includes('/assets/cutout')) {
      const u = opts.uploadUrl === undefined ? REMOTE : opts.uploadUrl
      return { ok: !!u, status: u ? 200 : 502, json: async () => ({ cutout_url: u }) }
    }
    if (method === 'GET') {
      return { ok: true, status: 200, json: async () => ({ come_closer_video_url: canonical }) }
    }
    // POST — 서버 멱등성 모사
    if (opts.postStatus && opts.postStatus !== 200) {
      return { ok: false, status: opts.postStatus, json: async () => ({}) }
    }
    if (opts.postBody) {
      return { ok: true, status: 200, json: async () => opts.postBody! }
    }
    if (canonical) {
      return { ok: true, status: 200,
        json: async () => ({ status: 'ready', generated: false, come_closer_video_url: canonical }) }
    }
    if (active) {
      return { ok: true, status: 200, json: async () => ({ status: 'processing', generated: false }) }
    }
    active = true
    return { ok: true, status: 200, json: async () => ({ status: 'processing', generated: true }) }
  }) as unknown as typeof fetch

  return {
    calls,
    submits: () => calls.filter(c => c.method === 'POST' && c.url.includes('/dev/come-closer')),
    uploads: () => calls.filter(c => c.url.includes('/assets/cutout')),
    promote: (u: string) => { canonical = u },
  }
}

const pipe = (contentId = 'cid-1', dog: string | null = REMOTE) => ({
  content_id: contentId, dog_only_nobg_url: dog, cutout_display_url: dog,
})

const P = (over: Partial<Parameters<typeof ensureComeCloser>[0]> = {}) => ({
  userId: 'u1', petId: 'pet_cid-1', pipeline: pipe(), ...over,
})

const setup = (o = {}) => { __resetAutogenGuards(); return stubBackend(o) }

// ── A. 첫 업로드, COME_CLOSER 없음 → 정확히 1회 제출 ────────────────────────

test('A: canonical 없음 → 정확히 1회 제출', async () => {
  const b = setup()
  const r = await ensureComeCloser(P())
  assert.equal(r.state, 'generating')
  assert.equal(b.submits().length, 1, '제출은 정확히 1회')
})

// ── B. 리렌더 / effect 두 번 실행 → 여전히 1회 ──────────────────────────────

test('B: 동시 이중 호출(StrictMode)도 제출 1회', async () => {
  const b = setup()
  const [r1, r2] = await Promise.all([ensureComeCloser(P()), ensureComeCloser(P())])
  assert.equal(b.submits().length, 1, '인플라이트 공유로 한 번만')
  assert.deepEqual(r1, r2, '두 호출이 같은 결과를 받는다')
})

test('B2: 순차 재호출도 재제출하지 않는다', async () => {
  const b = setup()
  await ensureComeCloser(P())
  await ensureComeCloser(P())
  await ensureComeCloser(P())
  assert.equal(b.submits().length, 1)
})

// ── C. 생성 중 새로고침 → 중복 제출 없음 ────────────────────────────────────

test('C: 새로고침(가드 초기화) 후에도 서버가 진행 중을 알려 재제출 안 함', async () => {
  const b = setup()
  await ensureComeCloser(P())
  assert.equal(b.submits().length, 1)

  __resetAutogenGuards()          // 새로고침 = 모듈 가드 소실
  const r = await ensureComeCloser(P())
  assert.equal(r.state, 'generating')
  // POST 는 한 번 더 나가지만 서버가 generated=false 로 막는다 — 프로바이더 호출 0.
  const second = b.submits()[1]
  assert.ok(second, '새로고침 후 POST 자체는 나갈 수 있다')
  assert.equal(b.submits().length, 2, '서버 멱등성이 최종 방어선이다')
})

// ── D. canonical 이미 존재 → 생성 없음 ──────────────────────────────────────

test('D: canonical 있으면 제출하지 않는다', async () => {
  const b = setup({ canonical: CANON })
  const r = await ensureComeCloser(P())
  assert.equal(r.state, 'ready')
  assert.equal(r.url, CANON)
  assert.equal(b.submits().length, 0, 'canonical 있으면 POST 자체가 없어야 한다')
})

// ── E/F. 새 업로드 → 새 키로 새 생성, 예전 자산 재사용 금지 ─────────────────

test('E: 새 content_id/pet_id 는 새 키로 새로 생성한다', async () => {
  const b = setup()
  await ensureComeCloser(P())
  const r = await ensureComeCloser(P({ petId: 'pet_cid-2', pipeline: pipe('cid-2') }))
  assert.equal(r.state, 'generating')
  assert.equal(b.submits().length, 2, '키가 다르면 각각 1회')
  assert.equal(b.submits()[1].body.pet_id, 'pet_cid-2')
})

test('F: 예전 펫에 자산이 있어도 새 펫은 그것을 쓰지 않는다', async () => {
  // GET 은 pet_id 별로 다르게 답한다 — 예전 펫만 canonical 보유.
  __resetAutogenGuards()
  const calls: Call[] = []
  ;(globalThis as any).sessionStorage = { getItem: () => null, setItem: () => {} }
  ;(globalThis as any).fetch = (async (url: string, init: any = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : null })
    if ((init.method || 'GET') === 'GET') {
      const old = url.includes('pet_id=pet_old')
      return { ok: true, status: 200,
        json: async () => ({ come_closer_video_url: old ? CANON : null }) }
    }
    return { ok: true, status: 200, json: async () => ({ status: 'processing', generated: true }) }
  }) as unknown as typeof fetch

  const r = await ensureComeCloser(P({ petId: 'pet_new', pipeline: pipe('cid-new') }))
  assert.equal(r.url, null, '예전 펫의 자산을 물려받으면 안 된다')
  assert.equal(r.state, 'generating')
  const post = calls.find(c => c.method === 'POST')
  assert.equal(post?.body.pet_id, 'pet_new')
})

// ── G. 새 펫 + 현재 선택된 테마로 생성 ──────────────────────────────────────

test('G: 제출 본문에 place 를 싣지 않는다 — 테마 독립', async () => {
  const b = setup()
  await ensureComeCloser(P({ petId: 'pet_new' }))
  assert.equal(b.submits()[0].body.selected_place_id, undefined)
})

test('G2: 같은 펫이면 테마와 무관하게 같은 키다', () => {
  assert.equal(comeCloserKey('u1', 'p'), 'u1|p|COME_CLOSER')
  assert.notEqual(comeCloserKey('u1', 'pA'), comeCloserKey('u1', 'pB'))
})

// ── H. 누끼가 data: URL → 1회 업로드 후 원격 URL 로 생성 ────────────────────

test('H: data: 누끼는 1회 업로드되고 원격 URL 로 제출된다', async () => {
  const b = setup()
  const r = await ensureComeCloser(P({ pipeline: pipe('cid-1', DATA_URL) }))
  assert.equal(r.state, 'generating')
  assert.equal(b.uploads().length, 1, '업로드는 1회')
  assert.equal(b.submits()[0].body.pet_image_url, REMOTE, '원격 URL 로 제출해야 한다')
})

test('H2: 이미 원격이면 업로드하지 않는다', async () => {
  const b = setup()
  await ensureComeCloser(P())
  assert.equal(b.uploads().length, 0)
})

test('H3: 업로드 실패면 제출하지 않고 error', async () => {
  const b = setup({ uploadUrl: null })
  const r = await ensureComeCloser(P({ pipeline: pipe('cid-1', DATA_URL) }))
  assert.equal(r.state, 'error')
  assert.equal(b.submits().length, 0, '원격 URL 없이 제출하면 stage=download 로 실패한다')
})

// ── I. 생성 실패 → 재시도는 하되 유한하게 ───────────────────────────────────

test('I: 제출 실패는 유한하게 재시도되고, 상한을 넘으면 멈춘다', async () => {
  // 예전 계약은 "실패하면 두 번 다시 제출하지 않는다" 였다. 그게 실제 장애를
  // 만들었다: 5xx·네트워크 한 번이면 그 이벤트는 세션 내내 제출되지 않은 채
  // 'generating' 으로 보고돼 영원히 생성 중처럼 보였다(작업 행은 하나도 없음).
  //
  // 지금 계약: 일시적 실패는 재시도한다. 단 MAX_SUBMIT_ATTEMPTS 로 상한을 둬서
  // 재제출 루프는 여전히 막는다.
  const b = setup({ postStatus: 502 })
  for (let i = 0; i < MAX_SUBMIT_ATTEMPTS + 3; i += 1) {
    const r = await ensureComeCloser(P())
    assert.equal(r.state, 'error', `시도 ${i + 1} 은 error 여야 한다`)
  }
  assert.equal(
    b.submits().length,
    MAX_SUBMIT_ATTEMPTS,
    '재시도는 상한까지만 — 무한 재제출 루프는 여전히 금지',
  )
})

test('I2: 실패를 generating 으로 보고하지 않는다', async () => {
  // 제출된 작업이 없는데 generating 이라고 하면 UI 가 영원히 기다린다.
  const b = setup({ postStatus: 502 })
  const r = await ensureComeCloser(P())
  assert.equal(r.state, 'error')
  assert.notEqual(r.state as string, 'generating')
  assert.equal(b.submits().length, 1)
})

test('I3: 큐 대기(queued)는 실패가 아니므로 상한을 소모하지 않는다', async () => {
  // queued 는 "아직 제출 안 됨" 이다. 슬롯이 빌 때까지 계속 다시 물어야 한다.
  const b = setup({ postBody: { status: 'queued' } })
  for (let i = 0; i < MAX_SUBMIT_ATTEMPTS + 3; i += 1) {
    const r = await ensureComeCloser(P())
    assert.equal(r.state, 'queued')
  }
  assert.equal(
    b.submits().length,
    MAX_SUBMIT_ATTEMPTS + 3,
    'queued 는 매번 다시 제출을 시도해야 한다',
  )
})

// ── 상태 노출 (요구사항 10) ────────────────────────────────────────────────

test('상태가 순서대로 통보된다 — 조용한 무반응이 없다', async () => {
  setup()
  const seen: ComeCloserState[] = []
  await ensureComeCloser(P({ onState: (s) => void seen.push(s) }))
  assert.deepEqual(seen, ['checking', 'generating'])
})

test('경로가 꺼져 있으면 unavailable', async () => {
  __resetAutogenGuards()
  ;(globalThis as any).fetch = (async () => ({ ok: false, status: 404, json: async () => ({}) })) as any
  const seen: ComeCloserState[] = []
  const r = await ensureComeCloser(P({ onState: (s) => void seen.push(s) }))
  assert.equal(r.state, 'unavailable')
  assert.ok(seen.includes('unavailable'))
})

test('신원이 없으면 아무 요청도 하지 않는다', async () => {
  const b = setup()
  const r = await ensureComeCloser(P({ userId: '  ' }))
  assert.equal(r.state, 'idle')
  assert.equal(b.calls.length, 0)
})

// ── 지원하지 않는 테마 (fresh_forest = 프론트 기본 테마) ────────────────────

test('지원하지 않는 place 는 unavailable — error 로 분류해 재시도하지 않는다', async () => {
  __resetAutogenGuards()
  const calls: string[] = []
  ;(globalThis as any).sessionStorage = { getItem: () => null, setItem: () => {} }
  ;(globalThis as any).fetch = (async (url: string, init: any = {}) => {
    calls.push(init.method || 'GET')
    if ((init.method || 'GET') === 'GET') {
      return { ok: true, status: 200, json: async () => ({ come_closer_video_url: null }) }
    }
    return {
      ok: false, status: 400,
      json: async () => ({ detail: { code: 'PLACE_NOT_SUPPORTED', supported: ['snow_forest'] } }),
    }
  }) as unknown as typeof fetch

  const r = await ensureComeCloser(P({ placeId: 'fresh_forest' }))
  assert.equal(r.state, 'unavailable', '재시도해도 성공할 수 없으므로 error 가 아니다')
  const posts = calls.filter(c => c === 'POST').length
  assert.equal(posts, 1)
})

// ── 기본 테마(fresh_forest) 지원 + 커스텀 배경 미지원 ───────────────────────

test('A: 새 펫 → 정확히 1회 제출 (테마 무관, fresh_forest 포함)', async () => {
  const b = setup()
  const r = await ensureComeCloser(P({ petId: 'pet_new' }))
  assert.equal(r.state, 'generating')
  assert.equal(b.submits().length, 1)
  // 테마를 아예 보내지 않으므로 fresh_forest·커스텀 배경도 자동으로 동작한다.
  assert.equal(b.submits()[0].body.selected_place_id, undefined)
})

test('B: fresh_forest 재호출(StrictMode/새로고침)도 1회', async () => {
  const b = setup()
  const p = P({ petId: 'pet_new' })
  await Promise.all([ensureComeCloser(p), ensureComeCloser(p)])
  await ensureComeCloser(p)
  assert.equal(b.submits().length, 1)
})

test('C: fresh_forest canonical 이 있으면 제출하지 않는다', async () => {
  const b = setup({ canonical: 'https://cdn/FF.mp4' })
  const r = await ensureComeCloser(P({ petId: 'pet_new' }))
  assert.equal(r.state, 'ready')
  assert.equal(b.submits().length, 0)
})

test('E: 커스텀 배경도 같은 자산을 재사용한다 (테마 독립)', async () => {
  const b = setup({ canonical: CANON })
  // 커스텀 배경이라는 개념이 요청에 아예 들어가지 않는다 → 항상 같은 자산.
  const r = await ensureComeCloser(P())
  assert.equal(r.url, CANON)
  assert.equal(b.submits().length, 0, '배경 종류는 생성 여부에 영향을 주지 않는다')
})

// ── 테마 독립 (제품 규칙) ───────────────────────────────────────────────────

test('B: 테마를 여러 번 바꿔도 추가 제출 0건', async () => {
  const b = setup()
  // 테마는 이제 인자가 아니다 — 화면에서 테마를 바꿔도 같은 호출이 반복될 뿐.
  await ensureComeCloser(P())
  await ensureComeCloser(P())
  await ensureComeCloser(P())
  assert.equal(b.submits().length, 1, '테마 전환은 프로바이더를 부르지 않는다')
})

test('D: canonical 이 생긴 뒤에는 어떤 화면에서도 재조회로 같은 URL', async () => {
  const b = setup({ canonical: CANON })
  for (let i = 0; i < 4; i += 1) {
    const r = await ensureComeCloser(P())
    assert.equal(r.url, CANON)
  }
  assert.equal(b.submits().length, 0)
})

test('조회 요청에 place_id 를 붙이지 않는다', async () => {
  const b = setup({ canonical: CANON })
  await ensureComeCloser(P())
  const get = b.calls.find(c => c.method === 'GET')!
  assert.ok(!get.url.includes('place_id'), `place_id 가 붙었다: ${get.url}`)
  assert.ok(get.url.includes('pet_id=pet_cid-1'))
})

test('F: 펫이 다르면 키가 달라 각각 생성된다', async () => {
  const b = setup()
  await ensureComeCloser(P({ petId: 'pet_a' }))
  await ensureComeCloser(P({ petId: 'pet_b', pipeline: pipe('cid-b') }))
  assert.equal(b.submits().length, 2)
})

// ── 업로드 교체 시퀀스 (실제로 더블탭이 죽었던 경로) ────────────────────────
//
//   pet A 캐시됨 → 새 업로드로 pet B → 파이프라인엔 아직 A URL
//   → B 조회가 그래도 돈다 → A URL 이 교체/제거된다 → B 액션이 붙는다

/** pet_id 별로 다르게 답하는 백엔드 스텁. */
function stubPerPet(assets: Record<string, string | null>) {
  const calls: Call[] = []
  const store = new Map<string, string>()
  ;(globalThis as any).sessionStorage = {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  }
  ;(globalThis as any).fetch = (async (url: string, init: any = {}) => {
    const method = init.method || 'GET'
    calls.push({ url, method, body: init.body ? JSON.parse(init.body) : null })
    if (method === 'GET') {
      const pet = new URL(url, 'http://x').searchParams.get('pet_id') || ''
      return { ok: true, status: 200, json: async () => ({ come_closer_video_url: assets[pet] ?? null }) }
    }
    return { ok: true, status: 200, json: async () => ({ status: 'processing', generated: true }) }
  }) as unknown as typeof fetch
  return { calls, submits: () => calls.filter(c => c.method === 'POST') }
}

test('시퀀스: pet A 캐시 → pet B 업로드 → B URL 로 교체되고 액션이 붙는다', async () => {
  __resetAutogenGuards()
  const b = stubPerPet({ pet_A: 'https://cdn/A.mp4', pet_B: 'https://cdn/B.mp4' })

  // 1) pet A 를 캐시한 상태의 파이프라인
  let pipeline: any = mergeComeCloserIntoPipeline(
    { content_id: 'cid-A', dog_only_nobg_url: REMOTE } as never, 'https://cdn/A.mp4', 'pet_A')
  assert.equal(pipeline.come_closer_video_url, 'https://cdn/A.mp4')
  assert.equal(isComeCloserCacheValid(pipeline, 'pet_A'), true)

  // 2) 새 업로드 → content/pet 이 B 로 바뀐다 (파이프라인엔 아직 A URL 이 남아 있다)
  pipeline = { ...pipeline, content_id: 'cid-B' }
  assert.equal(pipeline.come_closer_video_url, 'https://cdn/A.mp4', '아직 A URL 이 남아 있다')

  // 3) 캐시가 B 것이 아니므로 조회가 **그래도 돈다** (예전 가드면 여기서 멈췄다)
  assert.equal(isComeCloserCacheValid(pipeline, 'pet_B'), false)

  const r = await ensureComeCloser({ userId: 'u1', petId: 'pet_B', pipeline })
  assert.equal(r.url, 'https://cdn/B.mp4', 'B 의 자산을 받아와야 한다')

  // 4) A URL 이 B 로 교체된다
  pipeline = mergeComeCloserIntoPipeline(pipeline, r.url, 'pet_B')
  assert.equal(pipeline.come_closer_video_url, 'https://cdn/B.mp4')
  assert.equal(pipeline.come_closer_pet_id, 'pet_B')

  // 5) 액션이 붙는다 = PetIdleDisplay 에 넘어가는 actionSrc 가 B URL
  assert.ok(pipeline.come_closer_video_url, '액션 <video> 가 마운트될 수 있다')
  assert.equal(isComeCloserCacheValid(pipeline, 'pet_B'), true, '이제 B 캐시는 신뢰된다')
  assert.equal(b.submits().length, 0, 'B 자산이 이미 있으므로 생성은 없다')
})

test('시퀀스 하위 케이스: pet B 자산이 아직 없으면 A URL 을 붙이지 않고 비운다', async () => {
  __resetAutogenGuards()
  const b = stubPerPet({ pet_A: 'https://cdn/A.mp4', pet_B: null })

  let pipeline: any = mergeComeCloserIntoPipeline(
    { content_id: 'cid-A', dog_only_nobg_url: REMOTE } as never, 'https://cdn/A.mp4', 'pet_A')
  pipeline = { ...pipeline, content_id: 'cid-B' }

  const r = await ensureComeCloser({ userId: 'u1', petId: 'pet_B', pipeline })
  assert.equal(r.url, null, 'B 는 아직 자산이 없다')
  assert.equal(r.state, 'generating', '대신 B 용으로 1회 생성이 시작된다')
  assert.equal(b.submits().length, 1)

  // 화면은 A 의 클립을 붙이면 안 된다 — 다른 사진에서 만든 클립이라 펫이 바뀐다.
  pipeline = mergeComeCloserIntoPipeline(pipeline, null, null)
  assert.equal(pipeline.come_closer_video_url, null, 'A URL 이 제거돼야 한다')
  assert.equal(pipeline.come_closer_pet_id, null)
})

test('시퀀스: B 로 정착한 뒤에는 재조회하지 않는다 (루프 방지)', async () => {
  __resetAutogenGuards()
  const b = stubPerPet({ pet_B: 'https://cdn/B.mp4' })
  const pipeline: any = mergeComeCloserIntoPipeline(
    { content_id: 'cid-B', dog_only_nobg_url: REMOTE } as never, 'https://cdn/B.mp4', 'pet_B')
  assert.equal(isComeCloserCacheValid(pipeline, 'pet_B'), true)
  assert.equal(b.calls.length, 0, '유효한 캐시면 GET 조차 하지 않는다')
})
