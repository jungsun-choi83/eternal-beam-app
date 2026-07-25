/** Unity PetHologram packed clip: top half = alpha mask, bottom half = RGB (pre-multiplied) */

export type PackedAlphaScratch = {
  color: HTMLCanvasElement
  alpha: HTMLCanvasElement
  w: number
  h: number
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

export function isPackedAlphaVideo(video: HTMLVideoElement, src?: string): boolean {
  if (src && isLikelyPackedAlphaSource(src)) return true
  const w = video.videoWidth
  const h = video.videoHeight
  if (!w || !h) return false
  const frameH = h / 2
  return frameH >= w * 0.45 && h / w >= 1.05
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

  colorCtx.drawImage(video, 0, halfH, vw, halfH, 0, 0, vw, halfH)
  alphaCtx.drawImage(video, 0, 0, vw, halfH, 0, 0, vw, halfH)

  const color = colorCtx.getImageData(0, 0, vw, halfH)
  const alpha = alphaCtx.getImageData(0, 0, vw, halfH)
  const pixels = vw * halfH

  for (let i = 0; i < pixels; i++) {
    const i4 = i * 4
    // PetHologram.shader samples alpha mask .r; bottom RGB is pre-multiplied.
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
