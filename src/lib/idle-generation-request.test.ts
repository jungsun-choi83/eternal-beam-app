/**
 * 플로우 재배치 테스트 — 누끼 직후가 아니라 "확인" 시점에만 생성이 일어난다.
 *
 * 실행:  npm run test:flow
 *        (node:test + node:assert — 새 의존성 없음)
 *
 * React 컴포넌트를 렌더링하지 않고 검증할 수 있도록, 확인 버튼이 호출하는
 * 오케스트레이션(requestIdleGeneration)과 라우팅 가드(pending-generation)를
 * 순수 모듈로 분리해 두었다.
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  requestIdleGeneration,
  optionsCarryScene,
  sceneFieldsAreComplete,
  type GeneratePetVideoFn,
} from './idle-generation-request.ts'
import { sceneFormFields, type CanonicalScene } from './canonical-scene.ts'
import { canEnterDevicePlay, hasRealIdleVideo, isDemoIdleUrl } from './pending-generation.ts'

const FAKE_RESULT = {
  content_id: 'cid-1',
  dog_only_nobg_url: 'https://s/dog.png',
  idle_video_url: 'https://s/idle.mp4',
  action_video_url: null,
}

function spyGenerate() {
  const calls: { file: File; options: Record<string, unknown> }[] = []
  const fn: GeneratePetVideoFn = async (file, options) => {
    calls.push({ file, options: options as Record<string, unknown> })
    return FAKE_RESULT
  }
  return { fn, calls }
}

const cutFile = () => new File([new Uint8Array([1, 2, 3])], 'cutout.png', { type: 'image/png' })

// ── 확인 시 정확히 1회 생성 ─────────────────────────────────────────────────

test('확인 1회 → generatePetVideo 정확히 1회', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({ cutFile: cutFile(), contentId: 'cid-1', generate: fn })
  assert.equal(calls.length, 1)
})

test('확인을 두 번 부르면 두 번 — 중복 방지는 UI 가드의 책임', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({ cutFile: cutFile(), contentId: 'c', generate: fn })
  await requestIdleGeneration({ cutFile: cutFile(), contentId: 'c', generate: fn })
  assert.equal(calls.length, 2)
})

test('생성 결과를 그대로 돌려준다 (기존 백엔드 응답 형태 유지)', async () => {
  const { fn } = spyGenerate()
  const res = await requestIdleGeneration({ cutFile: cutFile(), contentId: 'c', generate: fn })
  assert.equal(res.idle_video_url, 'https://s/idle.mp4')
  assert.equal(res.content_id, 'cid-1')
})

// ── 백엔드로 나가는 인자 ────────────────────────────────────────────────────
//
// ⚠️ 이 절의 계약이 Phase 19 에서 **뒤집혔다.** 예전에는 "테마 데이터가 절대
// 새지 않는다"를 지켰다 — 배경이 프론트 전용 표시 레이어였기 때문이다. 이제는
// 승인된 장면이 생성 입력의 정본이므로, 장면을 **반드시 실어야** 한다.
// 다만 장면이 없으면 예전 인자 그대로 나가야 한다(레거시 호환).

test('userId 없이 부르면 옵션은 skipPreprocessing / contentId / idleOnly 뿐', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({ cutFile: cutFile(), contentId: 'cid-9', generate: fn })
  const opts = calls[0].options
  assert.deepEqual(Object.keys(opts).sort(), ['contentId', 'idleOnly', 'skipPreprocessing'])
  assert.equal(opts.skipPreprocessing, true)
  assert.equal(opts.idleOnly, true)
  assert.equal(opts.contentId, 'cid-9')
})

// 신원은 "어디에 저장할지"만 정한다. 테마와 달리 생성 결과를 바꾸지 않으므로
// 넘겨도 되고, 넘겨야 idle 과 COME_CLOSER 가 같은 user_id 아래 모인다.
test('userId 를 주면 그대로 전달된다 — 저장 경로가 anonymous 로 흩어지지 않게', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({
    cutFile: cutFile(), contentId: 'cid-9', userId: 'user_abc', generate: fn,
  })
  assert.equal(calls[0].options.userId, 'user_abc')
})

test('빈 userId 는 아예 넣지 않는다 — 백엔드 기본값을 덮어쓰면 안 된다', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({
    cutFile: cutFile(), contentId: 'c', userId: '   ', generate: fn,
  })
  assert.equal('userId' in calls[0].options, false)
})

const SCENE: CanonicalScene = {
  sceneId: 'scene_abc',
  contentId: 'c',
  backgroundType: 'theme',
  backgroundId: 'fresh_forest',
  petScale: 1.2,
  petX: 10,
  petY: -4,
  floorY: 0.88,
  shiftPct: -8,
  sceneKeyframeUrl: 'https://s/scene_abc.png',
  backgroundBaked: true,
}

test('장면이 없으면 예전 인자 그대로 — 레거시 클라이언트가 계속 동작한다', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({ cutFile: cutFile(), contentId: 'c', generate: fn })
  assert.equal('scene' in calls[0].options, false)
  assert.equal(optionsCarryScene(calls[0].options), false)
})

test('장면이 있으면 **한 벌로** 실린다 — 흩어진 테마 키가 아니라', async () => {
  const { fn, calls } = spyGenerate()
  await requestIdleGeneration({
    cutFile: cutFile(), contentId: 'c', scene: SCENE, generate: fn,
  })
  const opts = calls[0].options
  assert.equal(optionsCarryScene(opts), true)
  const scene = opts.scene as Record<string, string>
  assert.ok(sceneFieldsAreComplete(scene), `장면 필드가 불완전하다: ${JSON.stringify(scene)}`)
  assert.equal(scene.scene_keyframe_url, 'https://s/scene_abc.png')
  assert.equal(scene.background_type, 'theme')
  assert.equal(scene.background_baked, 'true')
})

test('세 가지 배경 갈래가 모두 같은 필드 모양으로 나간다', async () => {
  for (const backgroundType of ['original', 'theme', 'custom'] as const) {
    const { fn, calls } = spyGenerate()
    await requestIdleGeneration({
      cutFile: cutFile(),
      contentId: 'c',
      scene: { ...SCENE, backgroundType, backgroundId: backgroundType },
      generate: fn,
    })
    const scene = calls[0].options.scene as Record<string, string>
    assert.ok(sceneFieldsAreComplete(scene), backgroundType)
    assert.equal(scene.background_type, backgroundType)
  }
})

test('장면 필드 이름은 서버 폼 파라미터와 1:1 이다', () => {
  const fields = sceneFormFields(SCENE)
  assert.deepEqual(Object.keys(fields).sort(), [
    'background_baked', 'background_id', 'background_type',
    'pet_scale', 'pet_x', 'pet_y', 'scene_id', 'scene_keyframe_url',
  ])
})

test('업로드되는 파일은 확인 시점에 전달한 누끼 그대로', async () => {
  const { fn, calls } = spyGenerate()
  const f = cutFile()
  await requestIdleGeneration({ cutFile: f, contentId: 'c', generate: fn })
  assert.equal(calls[0].file, f)
})

// ── 누끼 직후에는 생성이 없다 (파이프라인 상태로 확인) ──────────────────────

test('누끼 직후 파이프라인에는 idle_video_url 이 비어 있다', () => {
  const afterCutout = { idle_video_url: '' }
  assert.equal(hasRealIdleVideo(afterCutout), false)
})

test('데모 mp4 는 실제 생성으로 치지 않는다', () => {
  assert.equal(isDemoIdleUrl('/demo/goya_idle_packed.mp4'), true)
  assert.equal(isDemoIdleUrl('https://device.eternalbeam.com/demo/goya_idle_packed.mp4'), true)
  assert.equal(hasRealIdleVideo({ idle_video_url: '/demo/goya_idle_packed.mp4' }), false)
  assert.equal(hasRealIdleVideo({ idle_video_url: 'https://s/idle_loop.mp4' }), true)
})

// ── devicePlay 가드 ─────────────────────────────────────────────────────────

test('실제 idle 이 없으면 devicePlay 진입 불가', () => {
  assert.equal(canEnterDevicePlay(null), false)
  assert.equal(canEnterDevicePlay({ idle_video_url: '' }), false)
  assert.equal(canEnterDevicePlay({ idle_video_url: '/demo/goya_idle_packed.mp4' }), false)
})

test('실제 idle 이 생기면 devicePlay 진입 가능', () => {
  assert.equal(canEnterDevicePlay({ idle_video_url: 'https://s/idle_loop.mp4' }), true)
})

test('명시적 데모 경로는 예외로 허용', () => {
  assert.equal(canEnterDevicePlay({ idle_video_url: '' }, { demo: true }), true)
  assert.equal(canEnterDevicePlay(null, { demo: true }), true)
})
