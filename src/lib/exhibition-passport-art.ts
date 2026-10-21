import type { PassportRecord } from './exhibition-passport-api.ts'

export const PASSPORT_ART_WIDTH = 1536
export const PASSPORT_ART_HEIGHT = 1024
export const PASSPORT_TEMPLATE_PATH = '/home/Elegant%20Gold%20Pet%20Passport%20Template%20Corrected.png'

/** Coordinates measured against the approved 1536 × 1024 template. */
export const PASSPORT_PHOTO_BOX = {
  x: 84,
  y: 227,
  width: 639,
  height: 657,
  radius: 24,
} as const

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
  language?: 'en' | 'ko'
}

/**
 * One SVG composition is used by the browser preview and by the downloaded
 * file. The supplied template owns all labels and decorations; this function
 * adds only the photo and four data values.
 */
export function buildPassportSvg(record: PassportRecord, options: PassportSvgOptions = {}): string {
  const templateHref = options.templateHref || PASSPORT_TEMPLATE_PATH
  const location = options.location || passportLocationLabel(record.exhibition_id)
  const issuedDate = formatPassportIssuedDate(record.created_at, options.language === 'ko' ? 'ko-KR' : 'en-US')
  const petNameAttributes = fittedTextAttributes(record.pet_name, 62, 34, 360)
  const locationAttributes = fittedTextAttributes(location, 38, 24, 350)
  const dateAttributes = fittedTextAttributes(issuedDate, 38, 24, 350)
  const photo = PASSPORT_PHOTO_BOX
  const photoHref = options.photoHref ? escapeXml(options.photoHref) : null

  const photoLayer = photoHref ? `
  <defs>
    <clipPath id="passportPhotoClip"><rect x="${photo.x}" y="${photo.y}" width="${photo.width}" height="${photo.height}" rx="${photo.radius}"/></clipPath>
    <filter id="passportPhotoBlur" x="-15%" y="-15%" width="130%" height="130%"><feGaussianBlur stdDeviation="16"/></filter>
  </defs>
  <g clip-path="url(#passportPhotoClip)">
    <rect x="${photo.x}" y="${photo.y}" width="${photo.width}" height="${photo.height}" fill="#17130d"/>
    <image href="${photoHref}" x="${photo.x - 30}" y="${photo.y - 30}" width="${photo.width + 60}" height="${photo.height + 60}" preserveAspectRatio="xMidYMid slice" opacity=".58" filter="url(#passportPhotoBlur)"/>
    <rect x="${photo.x}" y="${photo.y}" width="${photo.width}" height="${photo.height}" fill="#080908" opacity=".12"/>
    <image id="passport-photo" href="${photoHref}" x="${photo.x}" y="${photo.y}" width="${photo.width}" height="${photo.height}" preserveAspectRatio="xMidYMid meet"/>
  </g>` : ''

  return `<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" viewBox="0 0 ${PASSPORT_ART_WIDTH} ${PASSPORT_ART_HEIGHT}" role="img" aria-label="Eternal Beam Pet Passport for ${escapeXml(record.pet_name)}">
  <image href="${escapeXml(templateHref)}" x="0" y="0" width="${PASSPORT_ART_WIDTH}" height="${PASSPORT_ART_HEIGHT}" preserveAspectRatio="none"/>
  ${photoLayer}
  <text x="790" y="363" fill="#f4e7cc" font-family="Georgia, 'Times New Roman', serif" ${petNameAttributes}>${escapeXml(record.pet_name)}</text>
  <text x="790" y="540" fill="#f4e7cc" font-family="Georgia, 'Times New Roman', serif" font-size="54">${escapeXml(formatPassportId(record.queue_number))}</text>
  <text x="850" y="684" fill="#f4e7cc" font-family="Georgia, 'Times New Roman', serif" ${locationAttributes}>${escapeXml(location)}</text>
  <text x="850" y="830" fill="#f4e7cc" font-family="Georgia, 'Times New Roman', serif" ${dateAttributes}>${escapeXml(issuedDate)}</text>
</svg>`
}
