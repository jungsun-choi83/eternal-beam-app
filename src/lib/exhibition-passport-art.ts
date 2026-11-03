import type { PassportRecord } from './exhibition-passport-api.ts'

export const PASSPORT_ART_WIDTH = 1024
export const PASSPORT_ART_HEIGHT = 1536
export const PASSPORT_TEMPLATE_PATH = '/home/passport%20%282%29.png'
export const PASSPORT_MACHINE_INK = '#244b70'

/** Coordinates measured against the approved 1024 x 1536 vertical template. */
export const PASSPORT_PHOTO_BOX = {
  x: 176,
  y: 885,
  width: 296,
  height: 335,
  radius: 0,
} as const

/** The new artwork has a straight white photo opening; no rotation is needed. */
export const PASSPORT_PHOTO_ROTATION_DEGREES = 0
export const PASSPORT_PHOTO_CENTER = {
  x: PASSPORT_PHOTO_BOX.x + PASSPORT_PHOTO_BOX.width / 2,
  y: PASSPORT_PHOTO_BOX.y + PASSPORT_PHOTO_BOX.height / 2,
} as const

/** The image is contained inside the printed white photo opening. */
export const PASSPORT_PHOTO_IMAGE_BOX = PASSPORT_PHOTO_BOX

/** Inner opening of the lower-page photo frame in the supplied artwork. */
export const PASSPORT_PHOTO_APERTURE_PATH = [
  `M ${PASSPORT_PHOTO_BOX.x} ${PASSPORT_PHOTO_BOX.y}`,
  `H ${PASSPORT_PHOTO_BOX.x + PASSPORT_PHOTO_BOX.width}`,
  `V ${PASSPORT_PHOTO_BOX.y + PASSPORT_PHOTO_BOX.height}`,
  `H ${PASSPORT_PHOTO_BOX.x}`,
  'Z',
].join(' ')

// Compatibility exports for callers that share the art module. The new PNG
// already contains its frame artwork, so no foreground redraw is needed.
export const PASSPORT_PHOTO_FRAME_OUTER = PASSPORT_PHOTO_BOX
export const PASSPORT_PHOTO_OVERLAP_STAMP = ''

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
 * adds only the photo and four dynamic values.
 */
export function buildPassportSvg(record: PassportRecord, options: PassportSvgOptions = {}): string {
  const templateHref = options.templateHref || PASSPORT_TEMPLATE_PATH
  const petName = record.pet_name?.trim() || '—'
  const location = options.location || passportLocationLabel(record.exhibition_id)
  const issuedDate = formatPassportIssuedDateSeoul(record.created_at)
  const petId = options.petId || formatPassportId(record.queue_number)
  const valueX = 501
  const maxWidth = 320
  const photoHref = options.photoHref ? escapeXml(options.photoHref) : null
  const photoLayer = photoHref ? `
  <defs>
    <clipPath id="passportPhotoClip"><path d="${PASSPORT_PHOTO_APERTURE_PATH}"/></clipPath>
  </defs>
  <g clip-path="url(#passportPhotoClip)">
    <rect x="${PASSPORT_PHOTO_BOX.x}" y="${PASSPORT_PHOTO_BOX.y}" width="${PASSPORT_PHOTO_BOX.width}" height="${PASSPORT_PHOTO_BOX.height}" fill="#ffffff"/>
    <image id="passport-photo" href="${photoHref}" x="${PASSPORT_PHOTO_IMAGE_BOX.x}" y="${PASSPORT_PHOTO_IMAGE_BOX.y}" width="${PASSPORT_PHOTO_IMAGE_BOX.width}" height="${PASSPORT_PHOTO_IMAGE_BOX.height}" preserveAspectRatio="xMidYMid slice"/>
  </g>` : ''

  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" viewBox="0 0 ${PASSPORT_ART_WIDTH} ${PASSPORT_ART_HEIGHT}" role="img" aria-label="Eternal Beam Pet Passport for ${escapeXml(petName)}">
  <image href="${escapeXml(templateHref)}" x="0" y="0" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" preserveAspectRatio="none"/>
  ${photoLayer}
  <g fill="#10213a" font-family="Georgia, 'Times New Roman', serif">
    <text x="${valueX}" y="944" ${fittedTextAttributes(petName, 24, 15, maxWidth)}>${escapeXml(petName)}</text>
    <text x="${valueX}" y="1033" ${fittedTextAttributes(petId, 23, 15, maxWidth)}>${escapeXml(petId)}</text>
    <text x="${valueX}" y="1121" ${fittedTextAttributes(issuedDate, 22, 15, maxWidth)}>${escapeXml(issuedDate)}</text>
    <text x="${valueX}" y="1209" ${fittedTextAttributes(location, 22, 15, maxWidth)}>${escapeXml(location)}</text>
  </g>
</svg>`
}
