/**
 * 이미지 레이어 합성 유틸리티
 * 배경 + 메인 사진을 합치는 함수
 */

export interface LayerOptions {
  backgroundImage: string
  mainImage: string
  mainImageScale?: number
  hologramEffect?: boolean
  overlayOpacity?: number
}

export interface ComposedResult {
  dataUrl: string
  blob: Blob
  width: number
  height: number
}

const DEFAULT_SIZE = 1080
const MAIN_SCALE = 0.6

function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.lineTo(x + w - r, y)
  ctx.quadraticCurveTo(x + w, y, x + w, y + r)
  ctx.lineTo(x + w, y + h - r)
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h)
  ctx.lineTo(x + r, y + h)
  ctx.quadraticCurveTo(x, y + h, x, y + h - r)
  ctx.lineTo(x, y + r)
  ctx.quadraticCurveTo(x, y, x + r, y)
  ctx.closePath()
}

function drawBackgroundLayer(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  width: number,
  height: number,
) {
  const imgAspect = img.width / img.height
  const canvasAspect = width / height

  let drawWidth: number
  let drawHeight: number
  let offsetX: number
  let offsetY: number

  if (imgAspect > canvasAspect) {
    drawHeight = height
    drawWidth = img.width * (height / img.height)
    offsetX = (width - drawWidth) / 2
    offsetY = 0
  } else {
    drawWidth = width
    drawHeight = img.height * (width / img.width)
    offsetX = 0
    offsetY = (height - drawHeight) / 2
  }

  ctx.drawImage(img, offsetX, offsetY, drawWidth, drawHeight)
}

function drawMainPhotoLayer(
  ctx: CanvasRenderingContext2D,
  img: HTMLImageElement,
  width: number,
  height: number,
  scale: number,
) {
  const size = Math.min(width, height) * scale
  const x = (width - size) / 2
  const y = (height - size) / 2
  const radius = size * 0.1

  ctx.save()
  roundRect(ctx, x, y, size, size, radius)
  ctx.clip()
  ctx.drawImage(img, x, y, size, size)
  ctx.restore()
}

function drawHologramEffect(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  opacity: number,
) {
  const centerX = width / 2
  const centerY = height / 2
  const radius = Math.max(width, height) * 0.6

  const gradient = ctx.createRadialGradient(
    centerX,
    centerY,
    0,
    centerX,
    centerY,
    radius,
  )
  gradient.addColorStop(0, `rgba(102, 126, 234, ${opacity * 0.3})`)
  gradient.addColorStop(0.5, `rgba(0, 212, 255, ${opacity * 0.15})`)
  gradient.addColorStop(1, 'transparent')

  ctx.fillStyle = gradient
  ctx.fillRect(0, 0, width, height)
}

export function dataUrlToBlob(dataUrl: string): Blob {
  const arr = dataUrl.split(',')
  const mime = arr[0].match(/:(.*?);/)?.[1] ?? 'image/png'
  const bstr = atob(arr[1])
  const n = bstr.length
  const u8arr = new Uint8Array(n)
  for (let i = 0; i < n; i++) {
    u8arr[i] = bstr.charCodeAt(i)
  }
  return new Blob([u8arr], { type: mime })
}

export function createThumbnail(
  dataUrl: string,
  maxSize = 375,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      const canvas = document.createElement('canvas')
      const scale = Math.min(maxSize / img.width, maxSize / img.height)
      canvas.width = img.width * scale
      canvas.height = img.height * scale
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Canvas context not available'))
        return
      }
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
      resolve(canvas.toDataURL('image/jpeg', 0.8))
    }
    img.onerror = () => reject(new Error('Image load failed'))
    img.src = dataUrl
  })
}

export function composeImageLayers(
  options: LayerOptions,
): Promise<ComposedResult> {
  const {
    backgroundImage,
    mainImage,
    mainImageScale = MAIN_SCALE,
    hologramEffect = true,
    overlayOpacity = 0.2,
  } = options

  return new Promise((resolve, reject) => {
    const bgImg = new Image()
    const mainImg = new Image()
    bgImg.crossOrigin = 'anonymous'
    mainImg.crossOrigin = 'anonymous'

    let bgLoaded = false
    let mainLoaded = false

    const tryCompose = () => {
      if (!bgLoaded || !mainLoaded) return

      const canvas = document.createElement('canvas')
      canvas.width = DEFAULT_SIZE
      canvas.height = DEFAULT_SIZE
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('Canvas context not available'))
        return
      }

      drawBackgroundLayer(ctx, bgImg, DEFAULT_SIZE, DEFAULT_SIZE)
      drawMainPhotoLayer(ctx, mainImg, DEFAULT_SIZE, DEFAULT_SIZE, mainImageScale)

      if (hologramEffect) {
        drawHologramEffect(ctx, DEFAULT_SIZE, DEFAULT_SIZE, overlayOpacity)
      }

      const dataUrl = canvas.toDataURL('image/jpeg', 0.9)
      const blob = dataUrlToBlob(dataUrl)

      resolve({
        dataUrl,
        blob,
        width: DEFAULT_SIZE,
        height: DEFAULT_SIZE,
      })
    }

    bgImg.onload = () => {
      bgLoaded = true
      tryCompose()
    }
    bgImg.onerror = () => reject(new Error('Background image load failed'))
    bgImg.src = backgroundImage

    mainImg.onload = () => {
      mainLoaded = true
      tryCompose()
    }
    mainImg.onerror = () => reject(new Error('Main image load failed'))
    mainImg.src = mainImage
  })
}
