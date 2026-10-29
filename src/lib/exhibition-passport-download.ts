import {
  formatPassportIssuedDateSeoul,
  PASSPORT_ART_HEIGHT,
  PASSPORT_ART_WIDTH,
  PASSPORT_PHOTO_APERTURE_PATH,
  PASSPORT_PHOTO_BOX,
  PASSPORT_PHOTO_IMAGE_BOX,
  PASSPORT_TEMPLATE_PATH,
  passportLocationLabel,
} from './exhibition-passport-art.ts'
import type { PassportRecord } from './exhibition-passport-api.ts'

export class ExhibitionPassportDownloadError extends Error {
  constructor(message = 'The Passport photo could not be downloaded.') {
    super(message)
    this.name = 'ExhibitionPassportDownloadError'
  }
}

function base64(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

async function imageDataUrl(url: string, fetchImpl: typeof fetch): Promise<string> {
  let response: Response
  try {
    response = await fetchImpl(url, { cache: 'no-store' })
  } catch {
    throw new ExhibitionPassportDownloadError()
  }
  if (!response.ok) throw new ExhibitionPassportDownloadError()
  const blob = await response.blob()
  return `data:${blob.type || 'image/png'};base64,${base64(new Uint8Array(await blob.arrayBuffer()))}`
}

function loadImage(dataUrl: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    if (typeof Image === 'undefined') {
      reject(new ExhibitionPassportDownloadError())
      return
    }
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new ExhibitionPassportDownloadError())
    image.src = dataUrl
  })
}

function fitText(context: CanvasRenderingContext2D, text: string, x: number, y: number, maxWidth: number, size: number, family: string, color: string): void {
  let fontSize = size
  context.font = `${fontSize}px ${family}`
  while (context.measureText(text).width > maxWidth && fontSize > 16) {
    fontSize -= 1
    context.font = `${fontSize}px ${family}`
  }
  context.fillStyle = color
  context.fillText(text, x, y)
}

function drawCover(context: CanvasRenderingContext2D, image: HTMLImageElement): void {
  const scale = Math.max(PASSPORT_PHOTO_IMAGE_BOX.width / image.naturalWidth, PASSPORT_PHOTO_IMAGE_BOX.height / image.naturalHeight)
  const width = image.naturalWidth * scale
  const height = image.naturalHeight * scale
  context.save()
  clipPhotoAperture(context)
  context.fillStyle = '#ffffff'
  context.fillRect(PASSPORT_PHOTO_BOX.x, PASSPORT_PHOTO_BOX.y, PASSPORT_PHOTO_BOX.width, PASSPORT_PHOTO_BOX.height)
  context.drawImage(image, PASSPORT_PHOTO_IMAGE_BOX.x + (PASSPORT_PHOTO_IMAGE_BOX.width - width) / 2, PASSPORT_PHOTO_IMAGE_BOX.y + (PASSPORT_PHOTO_IMAGE_BOX.height - height) / 2, width, height)
  context.restore()
}

function clipPhotoAperture(context: CanvasRenderingContext2D): void {
  if (typeof Path2D !== 'undefined') {
    context.clip(new Path2D(PASSPORT_PHOTO_APERTURE_PATH))
    return
  }
  context.beginPath()
  tracePhotoAperture(context)
  context.clip()
}

function tracePhotoAperture(context: CanvasRenderingContext2D): void {
  context.rect(PASSPORT_PHOTO_BOX.x, PASSPORT_PHOTO_BOX.y, PASSPORT_PHOTO_BOX.width, PASSPORT_PHOTO_BOX.height)
}

function triggerPngDownload(doc: Document, png: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(png)
  const anchor = doc.createElement('a')
  const userAgent = doc.defaultView?.navigator.userAgent || ''
  const isAppleMobile = /iPad|iPhone|iPod/.test(userAgent)
    || (userAgent.includes('Macintosh') && (doc.defaultView?.navigator.maxTouchPoints || 0) > 1)

  anchor.href = objectUrl
  anchor.download = filename
  anchor.rel = 'noopener'
  if (isAppleMobile) {
    // iOS Safari does not reliably honor download= for Blob URLs. Opening the
    // PNG lets the visitor use the native Save Image action instead.
    anchor.removeAttribute('download')
    anchor.target = '_blank'
  }
  doc.body.appendChild(anchor)
  anchor.click()
  anchor.remove()

  // Keep the Blob URL alive while mobile browsers finish the download/open.
  const release = () => URL.revokeObjectURL(objectUrl)
  if (doc.defaultView) doc.defaultView.setTimeout(release, 60_000)
  else release()
}

export async function createPetPassportPng(
  record: PassportRecord,
  fetchImpl: typeof fetch = fetch,
  language: 'en' | 'ko' = 'en',
  petId = `#${String(record.queue_number).padStart(4, '0')}`,
  location = passportLocationLabel(record.exhibition_id),
): Promise<Blob> {
  void language
  if (!record.photo_url) throw new ExhibitionPassportDownloadError()
  if (typeof document === 'undefined') throw new ExhibitionPassportDownloadError()
  const [templateData, photoData] = await Promise.all([
    imageDataUrl(PASSPORT_TEMPLATE_PATH, fetchImpl),
    imageDataUrl(record.photo_url, fetchImpl),
  ])
  const [template, photo] = await Promise.all([loadImage(templateData), loadImage(photoData)])
  const canvas = document.createElement('canvas')
  canvas.width = PASSPORT_ART_WIDTH
  canvas.height = PASSPORT_ART_HEIGHT
  const context = canvas.getContext('2d')
  if (!context) throw new ExhibitionPassportDownloadError()

  context.drawImage(template, 0, 0, PASSPORT_ART_WIDTH, PASSPORT_ART_HEIGHT)
  drawCover(context, photo)

  const ink = '#10213a'
  const name = record.pet_name?.trim() || '—'
  fitText(context, name, 501, 944, 320, 24, 'Georgia, serif', ink)
  fitText(context, petId, 501, 1033, 320, 23, 'Georgia, serif', ink)
  fitText(context, formatPassportIssuedDateSeoul(record.created_at), 501, 1121, 320, 22, 'Georgia, serif', ink)
  fitText(context, location, 501, 1209, 320, 22, 'Georgia, serif', ink)

  return await new Promise((resolve, reject) => {
    canvas.toBlob((blob) => blob ? resolve(blob) : reject(new ExhibitionPassportDownloadError()), 'image/png')
  })
}

export async function downloadPetPassport(
  record: PassportRecord,
  fetchImpl: typeof fetch = fetch,
  documentImpl?: Document,
  language: 'en' | 'ko' = 'en',
  petId?: string,
  location?: string,
): Promise<void> {
  const doc = documentImpl || (typeof document !== 'undefined' ? document : null)
  if (!doc) throw new ExhibitionPassportDownloadError()
  const png = await createPetPassportPng(record, fetchImpl, language, petId, location || passportLocationLabel(record.exhibition_id))
  triggerPngDownload(doc, png, `pet-passport-${record.queue_number}.png`)
}

export async function sharePetPassport(
  record: PassportRecord,
  language: 'en' | 'ko' = 'en',
  petId?: string,
  location?: string,
): Promise<'shared' | 'downloaded'> {
  const png = await createPetPassportPng(record, fetch, language, petId, location || passportLocationLabel(record.exhibition_id))
  const filename = `pet-passport-${record.queue_number}.png`
  const file = typeof File !== 'undefined' ? new File([png], filename, { type: 'image/png' }) : null
  if (file && navigator.share && (!navigator.canShare || navigator.canShare({ files: [file] }))) {
    try {
      await navigator.share({ title: 'Pet Passport', files: [file] })
      return 'shared'
    } catch {
      // Some mobile browsers reject file sharing from local HTTP hosts or
      // after asynchronous image generation. Continue with the real PNG
      // download fallback instead of reporting a broken share action.
    }
  }
  triggerPngDownload(document, png, filename)
  return 'downloaded'
}
