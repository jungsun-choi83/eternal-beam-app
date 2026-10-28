import type { PassportRecord } from './exhibition-passport-api.ts'

export const PASSPORT_ART_WIDTH = 1536
export const PASSPORT_ART_HEIGHT = 1024
export const PASSPORT_TEMPLATE_PATH = '/home/Eternal%20Beam%20Travel%20Passport%20Duo%20Clean.png'
export const PASSPORT_MACHINE_INK = '#244b70'

/** Coordinates measured against the approved 1536 × 1024 template. */
export const PASSPORT_PHOTO_BOX = {
  x: 190,
  y: 312,
  width: 210,
  height: 283,
  radius: 0,
} as const

/** The slight clockwise tilt of the printed photo opening in the artwork. */
export const PASSPORT_PHOTO_ROTATION_DEGREES = 1.5
export const PASSPORT_PHOTO_CENTER = { x: 295, y: 453.5 } as const

/** Overscan used only for the live image so rotation cannot expose gaps. */
export const PASSPORT_PHOTO_IMAGE_BOX = {
  x: 184,
  y: 305,
  width: 222,
  height: 297,
} as const

/**
 * The actual inner opening of the baked photo frame. It is intentionally
 * separate from PASSPORT_PHOTO_BOX: the live image must follow the softly
 * rounded/chamfered paper opening, not expose a hard rectangular edge.
 */
export const PASSPORT_PHOTO_APERTURE_PATH = [
  'M 196 312',
  'Q 190 312 190 320',
  'L 190 580',
  'Q 190 588 198 588',
  'L 392 594',
  'Q 400 594 400 586',
  'L 400 320',
  'Q 400 318 392 318',
  'Z',
].join(' ')

/** Full baked frame region redrawn above the live photo. */
export const PASSPORT_PHOTO_FRAME_OUTER = {
  x: 154,
  y: 278,
  width: 286,
  height: 346,
} as const

/** Angled lower-left stamp that overlaps the photo opening in the artwork. */
export const PASSPORT_PHOTO_OVERLAP_STAMP = '38,546 338,612 311,716 12,650'

export function formatPassportId(queueNumber: number): string {
  return `#${String(queueNumber).padStart(4, '0')}`
}

export function formatPassportIssuedDate(
  createdAt: string | null | undefined,
  locale = 'en-US',
): string {
  if (!createdAt) return '—'
  const issuedAt = new Date(createdAt)
  if (Number.isNaN(issuedAt.getTime())) return '—'
  return new Intl.DateTimeFormat(locale, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  }).format(issuedAt)
}

/** Stable YYYY.MM.DD issue date in the exhibition's Asia/Seoul timezone. */
export function formatPassportIssuedDateSeoul(createdAt: string | null | undefined): string {
  if (!createdAt) return '—'
  const date = new Date(createdAt)
  if (Number.isNaN(date.getTime())) return '—'
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(date)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}.${values.month}.${values.day}`
}

export function formatPassportMachineDate(createdAt: string | null | undefined): string {
  return formatPassportIssuedDateSeoul(createdAt).replace(/\./g, '')
}

function machineValue(value: string, maxLength: number): string {
  return value.toUpperCase().replace(/[^A-Z0-9]/g, '<').slice(0, maxLength).padEnd(maxLength, '<')
}

export function passportMachineLines(
  petName: string | null | undefined,
  petId: string,
  createdAt: string | null | undefined,
): [string, string] {
  const name = machineValue(petName?.trim() || 'PET', 16)
  const id = machineValue(petId, 12)
  const date = formatPassportMachineDate(createdAt)
  return [
    `P<EBB${name}${'<'.repeat(28)}`.slice(0, 52),
    `P${id}${'<'.repeat(24)}${date}`.slice(0, 52),
  ]
}

export function passportLocationLabel(
  _exhibitionId: string | null | undefined,
  configuredLocation?: string | null,
): string {
  const location = configuredLocation?.trim()
  return location || 'Seoul, Korea'
}

function escapeXml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&apos;', '"': '&quot;',
  })[character] as string)
}

function fittedTextAttributes(value: string, baseSize: number, minimumSize: number, maxWidth: number): string {
  const estimatedWidth = value.length * baseSize * .55
  const fontSize = Math.max(minimumSize, Math.min(baseSize, Math.floor(maxWidth / Math.max(value.length * .55, 1))))
  const compressed = estimatedWidth > maxWidth
  return `font-size="${fontSize}"${compressed ? ` textLength="${maxWidth}" lengthAdjust="spacingAndGlyphs"` : ''}`
}

export type PassportSvgOptions = {
  templateHref?: string
  photoHref?: string | null
  location?: string
  petId?: string
  language?: 'en' | 'ko'
}

/**
 * One SVG composition is used by the browser preview and by the downloaded
 * file. The supplied template owns all labels and decorations; this function
 * adds only the photo and four data values.
 */
export function buildPassportSvg(record: PassportRecord, options: PassportSvgOptions = {}): string {
  const templateHref = options.templateHref || PASSPORT_TEMPLATE_PATH
  const petName = record.pet_name?.trim() || 'YOUR PET'
  const location = options.location || passportLocationLabel(record.exhibition_id)
  const issuedDate = formatPassportIssuedDate(record.created_at, options.language === 'ko' ? 'ko-KR' : 'en-US')
  const petId = options.petId || formatPassportId(record.queue_number)
  const petNameAttributes = fittedTextAttributes(petName, 30, 18, 250)
  const idAttributes = fittedTextAttributes(petId, 28, 18, 250)
  const dateAttributes = fittedTextAttributes(issuedDate, 27, 18, 250)
  const locationAttributes = fittedTextAttributes(location, 27, 18, 250)
  const photo = PASSPORT_PHOTO_BOX
  const frame = PASSPORT_PHOTO_FRAME_OUTER
  const photoHref = options.photoHref ? escapeXml(options.photoHref) : null
  const [machineLineOne, machineLineTwo] = passportMachineLines(petName, petId, record.created_at)
  const dynamicLocationLayer = location.trim().toUpperCase() === 'SEOUL, KOREA'
    ? ''
    : `<rect x="438" y="565" width="310" height="62" fill="#f5e8cb" opacity=".94"/>
  <text x="450" y="606" fill="#10213a" font-family="Georgia, 'Times New Roman', serif" ${locationAttributes}>${escapeXml(location)}</text>`

  const photoLayer = photoHref ? `
  <defs>
    <clipPath id="passportPhotoClip"><path d="${PASSPORT_PHOTO_APERTURE_PATH}"/></clipPath>
    <filter id="passportPhotoBlur" x="-15%" y="-15%" width="130%" height="130%"><feGaussianBlur stdDeviation="16"/></filter>
    <mask id="passportPhotoFrameMask" maskUnits="userSpaceOnUse" x="0" y="0" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}">
      <rect width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" fill="black"/>
      <rect x="${frame.x}" y="${frame.y}" width="${frame.width}" height="${frame.height}" fill="white"/>
      <path d="${PASSPORT_PHOTO_APERTURE_PATH}" fill="black"/>
      <polygon points="${PASSPORT_PHOTO_OVERLAP_STAMP}" fill="white"/>
    </mask>
  </defs>
  <g clip-path="url(#passportPhotoClip)">
    <rect x="${photo.x}" y="${photo.y}" width="${photo.width}" height="${photo.height}" fill="#eee3cc"/>
    <image id="passport-photo" href="${photoHref}" x="${PASSPORT_PHOTO_IMAGE_BOX.x}" y="${PASSPORT_PHOTO_IMAGE_BOX.y}" width="${PASSPORT_PHOTO_IMAGE_BOX.width}" height="${PASSPORT_PHOTO_IMAGE_BOX.height}" transform="rotate(${PASSPORT_PHOTO_ROTATION_DEGREES} ${PASSPORT_PHOTO_CENTER.x} ${PASSPORT_PHOTO_CENTER.y})" preserveAspectRatio="xMidYMid slice"/>
  </g>
  <image href="${escapeXml(templateHref)}" x="0" y="0" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" preserveAspectRatio="none" mask="url(#passportPhotoFrameMask)"/>` : ''

  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" viewBox="0 0 ${PASSPORT_ART_WIDTH} ${PASSPORT_ART_HEIGHT}" role="img" aria-label="Eternal Beam Pet Passport for ${escapeXml(petName)}">
  <image href="${escapeXml(templateHref)}" x="0" y="0" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" preserveAspectRatio="none"/>
  ${photoLayer}
  <text x="450" y="374" fill="#10213a" font-family="Georgia, 'Times New Roman', serif" ${petNameAttributes}>${escapeXml(petName)}</text>
  <text x="450" y="449" fill="#10213a" font-family="Georgia, 'Times New Roman', serif" ${idAttributes}>${escapeXml(petId)}</text>
  <text x="450" y="521" fill="#10213a" font-family="Georgia, 'Times New Roman', serif" ${dateAttributes}>${escapeXml(issuedDate)}</text>
  ${dynamicLocationLayer}
  <g fill="${PASSPORT_MACHINE_INK}" font-family="'Courier New', monospace" font-size="20" letter-spacing="1">
    <text x="104" y="884">${escapeXml(machineLineOne)}</text>
    <text x="104" y="921">${escapeXml(machineLineTwo)}</text>
  </g>
</svg>`
}
