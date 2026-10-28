import {
  formatPassportIssuedDateSeoul,
  PASSPORT_ART_HEIGHT,
  PASSPORT_ART_WIDTH,
  PASSPORT_MACHINE_INK,
  PASSPORT_PHOTO_APERTURE_PATH,
  PASSPORT_PHOTO_BOX,
  PASSPORT_PHOTO_CENTER,
  PASSPORT_PHOTO_IMAGE_BOX,
  PASSPORT_PHOTO_FRAME_OUTER,
  PASSPORT_PHOTO_OVERLAP_STAMP,
  PASSPORT_PHOTO_ROTATION_DEGREES,
  PASSPORT_TEMPLATE_PATH,
  passportLocationLabel,
  passportMachineLines,
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
  context.fillStyle = '#eee3cc'
  context.fillRect(PASSPORT_PHOTO_BOX.x, PASSPORT_PHOTO_BOX.y, PASSPORT_PHOTO_BOX.width, PASSPORT_PHOTO_BOX.height)
  context.translate(PASSPORT_PHOTO_CENTER.x, PASSPORT_PHOTO_CENTER.y)
  context.rotate(PASSPORT_PHOTO_ROTATION_DEGREES * Math.PI / 180)
  context.translate(-PASSPORT_PHOTO_CENTER.x, -PASSPORT_PHOTO_CENTER.y)
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
  // Same geometry as PASSPORT_PHOTO_APERTURE_PATH, expressed with Canvas
  // calls so the downloaded PNG uses exactly the SVG preview's aperture.
  context.moveTo(196, 312)
  context.quadraticCurveTo(190, 312, 190, 320)
  context.lineTo(190, 580)
  context.quadraticCurveTo(190, 588, 198, 588)
  context.lineTo(392, 594)
  context.quadraticCurveTo(400, 594, 400, 586)
  context.lineTo(400, 320)
  context.quadraticCurveTo(400, 318, 392, 318)
  context.closePath()
}

/** Redraw the baked frame outside the photo opening so its corners stay above the photo. */
function drawFrameOverlay(context: CanvasRenderingContext2D, template: HTMLImageElement): void {
  context.save()
  context.beginPath()
  context.rect(PASSPORT_PHOTO_FRAME_OUTER.x, PASSPORT_PHOTO_FRAME_OUTER.y, PASSPORT_PHOTO_FRAME_OUTER.width, PASSPORT_PHOTO_FRAME_OUTER.height)
  tracePhotoAperture(context)
  context.clip('evenodd')
  context.drawImage(template, 0, 0, PASSPORT_ART_WIDTH, PASSPORT_ART_HEIGHT)
  context.restore()

  const stampPoints = PASSPORT_PHOTO_OVERLAP_STAMP.split(' ').map((point) => point.split(',').map(Number))
  context.save()
  context.beginPath()
  stampPoints.forEach(([x, y], index) => index === 0 ? context.moveTo(x, y) : context.lineTo(x, y))
  context.closePath()
  context.clip()
  context.drawImage(template, 0, 0, PASSPORT_ART_WIDTH, PASSPORT_ART_HEIGHT)
  context.restore()
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
  if (location.trim().toUpperCase() !== 'SEOUL, KOREA') {
    context.fillStyle = '#f5e8cb'
    context.fillRect(438, 565, 310, 62)
  }
  drawCover(context, photo)
  drawFrameOverlay(context, template)

  const ink = '#10213a'
  const name = record.pet_name?.trim() || '—'
  fitText(context, name, 450, 374, 250, 30, 'Georgia, serif', ink)
  fitText(context, petId, 450, 449, 250, 28, 'Georgia, serif', ink)
  fitText(context, formatPassportIssuedDateSeoul(record.created_at), 450, 521, 250, 27, 'Georgia, serif', ink)
  if (location.trim().toUpperCase() !== 'SEOUL, KOREA') fitText(context, location, 450, 606, 250, 27, 'Georgia, serif', ink)

  const [machineLineOne, machineLineTwo] = passportMachineLines(name, petId, record.created_at)
  context.fillStyle = PASSPORT_MACHINE_INK
  context.font = '20px "Courier New", monospace'
  context.fillText(machineLineOne, 104, 884)
  context.fillText(machineLineTwo, 104, 921)

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
  const objectUrl = URL.createObjectURL(png)
  const anchor = doc.createElement('a')
  anchor.href = objectUrl
  anchor.download = `pet-passport-${record.queue_number}.png`
  anchor.rel = 'noopener'
  doc.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
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
    await navigator.share({ title: 'Pet Passport', files: [file] })
    return 'shared'
  }
  const objectUrl = URL.createObjectURL(png)
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(objectUrl)
  return 'downloaded'
}
