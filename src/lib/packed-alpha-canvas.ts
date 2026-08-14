/** Web packed clip (vstack): top half = RGB, bottom half = alpha mask (pre-multiplied RGB). */

export type PackedAlphaScratch = {
  color: HTMLCanvasElement
  alpha: HTMLCanvasElement
  w: number
  h: number
  /** Cached once per clip size — alpha mask is the lower-chroma half. */
  rgbOnTop?: boolean
}

export function createPackedAlphaScratch(): PackedAlphaScratch {
  return {
    color: document.createElement('canvas'),
    alpha: document.createElement('canvas'),
    w: 0,
    h: 0,
  }
}

export function isLikelyPackedAlphaSource(src: string): boolean {
  const path = src.split('?')[0].split('#')[0].toLowerCase()
  return /(^|\/)demo\/.*packed.*\.mp4$/.test(path) || /_packed\.mp4$/.test(path)
}

/**
 * 알파 매트 후보로 인정할 최대 평균 chroma.
 *
 * 진짜 vstack 매트 절반은 원본이 그레이스케일(R=G=B)이라 chroma가 0이어야 하지만,
 * H.264 4:2:0 크로마 서브샘플링과 압축 링잉 때문에 조금 뜬다. 실측값:
 *
 *   goya_idle_packed.mp4  (진짜 packed)  매트 절반 chroma = 3.68
 *   goya_touch_packed.mp4 (진짜 packed)  매트 절반 chroma = 3.69
 *   Shiba 세로 720x1180   (평범한 영상)  덜 화려한 절반 = 13.77
 *
 * 6.0 은 실측 매트값(3.7) 위로 여유를 두면서 평범한 영상의 최저 절반(13.8)과는
 * 2.3배 떨어져 있다. 일부러 낮게 잡았다 — 임계값이 낮을수록 packed 판정이
 * 어려워지고, 평범한 영상이 반토막 나는 사고(false positive)를 피할 수 있다.
 */
const ALPHA_MATTE_MAX_CHROMA = 6.0

/**
 * 컬러 절반은 매트 절반보다 이만큼은 더 화려해야 한다.
 * 진짜 packed 실측 비율은 10.75/3.68 = 2.92. 흑백 영상처럼 양쪽 절반이 모두
 * 무채색이면 비율이 1 근처가 되어 packed 로 오인되지 않고 plain 으로 떨어진다.
 */
const MIN_COLOR_TO_MATTE_RATIO = 2.0

/** 절반별 평균 chroma 를 재는 함수. 픽셀을 못 읽으면(CORS taint 등) null. */
export type HalfChromaSampler = () => { top: number; bottom: number } | null

/**
 * packed 판정 규칙 (순수 함수 — DOM 없이 테스트 가능).
 *
 * 예전에는 종횡비만 보고 판정해서 세로 Luma 영상(720x1180 → h/w 1.64,
 * 720x1280 → 1.78)이 packed 창(1.0~2.5) 안에 들어가 반토막이 났다. 이제
 * 종횡비는 "필요조건"으로만 쓰고, 실제 판정은 내용(chroma)이 한다.
 */
export function decidePackedAlpha(
  width: number,
  height: number,
  src: string | undefined,
  sampleChroma: HalfChromaSampler,
): boolean {
  // 1) 파일명이 packed 라고 명시하면 그대로 신뢰 (빠른 양성).
  if (src && isLikelyPackedAlphaSource(src)) return true

  // 2) 기하학적 필요조건. vstack 은 절반 두 개를 위아래로 쌓으므로 높이가
  //    짝수여야 하고, 절대 가로보다 납작할 수 없다(landscape 는 여기서 탈락).
  if (!width || !height || height % 2 !== 0) return false
  if (height / width < 1.0) return false

  // 3) 내용 기반 판정. 못 읽으면 packed 로 추정하지 않는다 —
  //    평범한 영상을 반토막 내는 것보다 packed 를 놓치는 편이 안전하다.
  const chroma = sampleChroma()
  if (!chroma) return false

  const lo = Math.min(chroma.top, chroma.bottom)
  const hi = Math.max(chroma.top, chroma.bottom)

  // 매트로 볼 만큼 무채색인 절반이 없으면 평범한 영상이다.
  if (lo > ALPHA_MATTE_MAX_CHROMA) return false
  // 양쪽 다 무채색이면(흑백 영상 등) 어느 쪽이 매트인지 알 수 없다 → plain.
  if (hi < lo * MIN_COLOR_TO_MATTE_RATIO) return false

  return true
}

/** 비디오 프레임의 위/아래 절반 평균 chroma 를 잰다. taint/미디어 미준비 시 null. */
function sampleHalfChroma(video: HTMLVideoElement): { top: number; bottom: number } | null {
  const vw = video.videoWidth
  const halfH = Math.floor(video.videoHeight / 2)
  if (!vw || !halfH || video.readyState < 2) return null

  // 평균만 필요하므로 축소해서 잰다. 보간을 끄는 게 중요하다 — 스무딩은 이웃한
  // 보색을 섞어 chroma 를 실제보다 낮게 만들고, 그러면 평범한 영상이 매트로
  // 오인될 수 있다. nearest-neighbour 서브샘플링은 평균에 대해 편향이 없다.
  const scale = Math.min(1, 192 / vw)
  const sw = Math.max(1, Math.round(vw * scale))
  const sh = Math.max(1, Math.round(halfH * scale))

  const canvas = document.createElement('canvas')
  canvas.width = sw
  canvas.height = sh
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  if (!ctx) return null
  ctx.imageSmoothingEnabled = false

  try {
    ctx.drawImage(video, 0, 0, vw, halfH, 0, 0, sw, sh)
    const top = averageChroma(ctx.getImageData(0, 0, sw, sh).data)
    ctx.clearRect(0, 0, sw, sh)
    ctx.drawImage(video, 0, halfH, vw, halfH, 0, 0, sw, sh)
    const bottom = averageChroma(ctx.getImageData(0, 0, sw, sh).data)
    return { top, bottom }
  } catch {
    // cross-origin 비디오면 getImageData 가 SecurityError 를 던진다.
    return null
  }
}

export function isPackedAlphaVideo(video: HTMLVideoElement, src?: string): boolean {
  return decidePackedAlpha(video.videoWidth, video.videoHeight, src, () =>
    sampleHalfChroma(video),
  )
}

function configureCanvasQuality(ctx: CanvasRenderingContext2D) {
  ctx.imageSmoothingEnabled = true
  ctx.imageSmoothingQuality = 'high'
}

function ensureScratchSize(scratch: PackedAlphaScratch, vw: number, halfH: number) {
  if (scratch.w === vw && scratch.h === halfH) return
  scratch.color.width = vw
  scratch.color.height = halfH
  scratch.alpha.width = vw
  scratch.alpha.height = halfH
  scratch.w = vw
  scratch.h = halfH
  scratch.rgbOnTop = undefined
}

function averageChroma(data: Uint8ClampedArray): number {
  let sum = 0
  const pixels = data.length / 4
  if (pixels === 0) return 0
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i]
    const g = data[i + 1]
    const b = data[i + 2]
    sum += Math.abs(r - g) + Math.abs(g - b) + Math.abs(r - b)
  }
  return sum / pixels
}

function detectRgbOnTop(
  colorCtx: CanvasRenderingContext2D,
  alphaCtx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  vw: number,
  halfH: number,
): boolean {
  alphaCtx.drawImage(video, 0, 0, vw, halfH, 0, 0, vw, halfH)
  colorCtx.drawImage(video, 0, halfH, vw, halfH, 0, 0, vw, halfH)
  const topChroma = averageChroma(alphaCtx.getImageData(0, 0, vw, halfH).data)
  const bottomChroma = averageChroma(colorCtx.getImageData(0, 0, vw, halfH).data)
  return topChroma >= bottomChroma
}

export function drawPackedAlphaVideo(
  destCtx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
  scratch: PackedAlphaScratch,
) {
  const vw = video.videoWidth
  const halfH = Math.floor(video.videoHeight / 2)
  if (!vw || !halfH || video.readyState < 2) return

  ensureScratchSize(scratch, vw, halfH)

  const colorCtx = scratch.color.getContext('2d', { willReadFrequently: true })
  const alphaCtx = scratch.alpha.getContext('2d', { willReadFrequently: true })
  if (!colorCtx || !alphaCtx) return

  configureCanvasQuality(colorCtx)
  configureCanvasQuality(alphaCtx)
  configureCanvasQuality(destCtx)

  if (scratch.rgbOnTop === undefined) {
    scratch.rgbOnTop = detectRgbOnTop(colorCtx, alphaCtx, video, vw, halfH)
  }

  const rgbY = scratch.rgbOnTop ? 0 : halfH
  const alphaY = scratch.rgbOnTop ? halfH : 0

  colorCtx.drawImage(video, 0, rgbY, vw, halfH, 0, 0, vw, halfH)
  alphaCtx.drawImage(video, 0, alphaY, vw, halfH, 0, 0, vw, halfH)

  const color = colorCtx.getImageData(0, 0, vw, halfH)
  const alpha = alphaCtx.getImageData(0, 0, vw, halfH)
  const pixels = vw * halfH

  for (let i = 0; i < pixels; i++) {
    const i4 = i * 4
    const aByte = alpha.data[i4]
    color.data[i4 + 3] = aByte
    if (aByte > 0) {
      const invA = 255 / aByte
      color.data[i4] = Math.min(255, Math.round(color.data[i4] * invA))
      color.data[i4 + 1] = Math.min(255, Math.round(color.data[i4 + 1] * invA))
      color.data[i4 + 2] = Math.min(255, Math.round(color.data[i4 + 2] * invA))
    }
  }

  colorCtx.putImageData(color, 0, 0)
  destCtx.drawImage(scratch.color, 0, 0, vw, halfH, dx, dy, dw, dh)
}

/** CORS/taint fallback — RGB half only (no alpha read); hides vstack bottom mask. */
export function drawPackedRgbHalfOnly(
  destCtx: CanvasRenderingContext2D,
  video: HTMLVideoElement,
  dx: number,
  dy: number,
  dw: number,
  dh: number,
  scratch: PackedAlphaScratch,
) {
  const vw = video.videoWidth
  const halfH = Math.floor(video.videoHeight / 2)
  if (!vw || !halfH || video.readyState < 2) return

  ensureScratchSize(scratch, vw, halfH)
  const colorCtx = scratch.color.getContext('2d')
  if (!colorCtx) return

  configureCanvasQuality(colorCtx)
  configureCanvasQuality(destCtx)

  const rgbY = scratch.rgbOnTop === false ? halfH : 0
  colorCtx.clearRect(0, 0, vw, halfH)
  colorCtx.drawImage(video, 0, rgbY, vw, halfH, 0, 0, vw, halfH)
  destCtx.drawImage(scratch.color, 0, 0, vw, halfH, dx, dy, dw, dh)
}
