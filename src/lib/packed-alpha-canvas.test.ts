/**
 * packed-alpha 판정 테스트.
 *
 * 실행:  npm run test:renderer
 *        (node:test + node:assert — 새 의존성 없음. Node 22+ 의 TS strip 사용.)
 *
 * 배경: 예전 isPackedAlphaVideo() 는 종횡비만 보고 판정해서 세로 Luma mp4
 * (720x1180 → h/w 1.64)가 packed 창(1.0~2.5) 안에 들어갔다. 그 결과 프레임이
 * 반으로 잘려 아래쪽 절반만, 그것도 색이 뭉개진 채로 렌더링됐다.
 *
 * 아래 chroma 값은 지어낸 게 아니라 실제 파일에서 측정한 값이다:
 *   ffmpeg -i <clip> -frames:v 1 frame.png  후 각 절반의
 *   mean(|r-g| + |g-b| + |r-b|)  (averageChroma() 와 동일한 식)
 */

import { test } from 'node:test'
import assert from 'node:assert/strict'

import { decidePackedAlpha, isLikelyPackedAlphaSource } from './packed-alpha-canvas.ts'

/** 측정된 절반별 chroma 를 그대로 돌려주는 샘플러. */
const chroma = (top: number, bottom: number) => () => ({ top, bottom })

/** 픽셀을 못 읽는 경우(cross-origin taint 등). */
const unreadable = () => null

/** 호출되면 기록하는 샘플러 — 기하학 단계에서 걸러졌는지 확인용. */
function spySampler(top: number, bottom: number) {
  const state = { calls: 0 }
  const fn = () => {
    state.calls += 1
    return { top, bottom }
  }
  return { fn, state }
}

// ── 평범한 영상은 절대 반토막 나면 안 된다 ─────────────────────────────────

test('세로 720x1180 (실측 Shiba idle mp4) → plain', () => {
  // 측정: top 29.67 / bottom 13.77 — 양쪽 다 유의미한 chroma.
  assert.equal(decidePackedAlpha(720, 1180, undefined, chroma(29.67, 13.77)), false)
})

test('세로 720x1280 (Luma ray-2 세로 출력) → plain', () => {
  // 합성값: 실제 Luma 세로 클립이 아직 없어 평범한 사진 영상 수준으로 잡음.
  // 핵심은 h/w = 1.78 이 옛 packed 창 안이라는 점 — 이제 chroma 가 결정한다.
  assert.equal(decidePackedAlpha(720, 1280, undefined, chroma(24.5, 11.9)), false)
})

test('가로 1280x720 → plain (픽셀을 읽기도 전에 기하학에서 탈락)', () => {
  const { fn, state } = spySampler(10.75, 3.68)
  assert.equal(decidePackedAlpha(1280, 720, undefined, fn), false)
  assert.equal(state.calls, 0, 'landscape 는 chroma 샘플링 없이 걸러져야 한다')
})

// ── 진짜 packed 클립은 계속 동작해야 한다 ──────────────────────────────────

test('goya_idle_packed.mp4 1284x1432 (실측) → packed', () => {
  // 측정: top 10.75 / bottom 3.68 — 아래 절반이 그레이스케일 매트.
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(10.75, 3.68)), true)
})

test('goya_touch_packed.mp4 1284x1432 (실측) → packed', () => {
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(10.93, 3.69)), true)
})

test('매트가 위쪽 절반이어도 packed (순서 무관)', () => {
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(3.68, 10.75)), true)
})

// ── 파일명 기반 빠른 양성 ──────────────────────────────────────────────────

test('파일명이 packed 면 chroma 를 재지 않고 packed', () => {
  const { fn, state } = spySampler(29.67, 13.77) // 내용상으로는 plain 인데도
  assert.equal(decidePackedAlpha(720, 1180, '/demo/goya_idle_packed.mp4', fn), true)
  assert.equal(state.calls, 0, '파일명 양성이면 샘플링을 건너뛴다')
})

test('isLikelyPackedAlphaSource 는 쿼리스트링이 붙어도 인식한다', () => {
  assert.equal(isLikelyPackedAlphaSource('/demo/goya_idle_packed.mp4?v=2'), true)
  assert.equal(isLikelyPackedAlphaSource('https://x.supabase.co/idle_loop.mp4'), false)
})

// ── 애매하면 plain (false-positive packed 금지) ────────────────────────────

test('양쪽 절반 모두 무채색(흑백 영상) → plain', () => {
  // 낮은 절반은 매트 후보지만 컬러 절반이 충분히 화려하지 않다 → 판단 불가 → plain.
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(2.4, 2.0)), false)
})

test('매트 후보는 있으나 대비가 2배 미만 → plain', () => {
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(9.0, 5.0)), false)
})

test('픽셀을 못 읽으면(CORS taint) → plain', () => {
  assert.equal(decidePackedAlpha(1284, 1432, undefined, unreadable), false)
})

test('홀수 높이는 반으로 쪼갤 수 없다 → plain', () => {
  assert.equal(decidePackedAlpha(720, 1181, undefined, chroma(10.75, 3.68)), false)
})

test('크기 정보가 없으면 → plain', () => {
  assert.equal(decidePackedAlpha(0, 0, undefined, chroma(10.75, 3.68)), false)
})

// ── 임계값 경계 ────────────────────────────────────────────────────────────

test('매트 chroma 임계값 6.0 경계', () => {
  // 6.0 이하 + 컬러 절반이 2배 이상 → packed
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(20, 6.0)), true)
  // 6.0 초과 → 매트로 인정하지 않음
  assert.equal(decidePackedAlpha(1284, 1432, undefined, chroma(20, 6.01)), false)
})

// ── scripts/pack_alpha_video.py 산출물 (실측) ──────────────────────────────
// clip_08.mp4 (480x832, Wan) → clip_08_packed.mp4 (480x1664).
// 상단 premultiplied RGB / 하단 grayscale 매트. 측정값은 실제 파일에서 뽑았다:
//   ffmpeg -ss 0.2 -frames:v 1 → 절반별 mean(|r-g|+|g-b|+|r-b|)

test('pack_alpha_video.py 산출물 480x1664 (실측) → packed', () => {
  assert.equal(decidePackedAlpha(480, 1664, undefined, chroma(28.41, 0.0)), true)
})

test('packed 산출물은 파일명 규약으로도 잡힌다', () => {
  assert.equal(isLikelyPackedAlphaSource('/x/clip_08_packed.mp4'), true)
  assert.equal(isLikelyPackedAlphaSource('/x/clip_08.mp4'), false)
})

test('원본 clip_08.mp4 480x832 (실측) → plain (반토막 금지)', () => {
  // 같은 클립의 packed 이전 상태. 측정: top 36.97 / bottom 14.92 —
  // 아래 절반도 유채색(14.92 > 6.0)이라 매트로 인정되지 않는다.
  assert.equal(decidePackedAlpha(480, 832, undefined, chroma(36.97, 14.92)), false)
})
