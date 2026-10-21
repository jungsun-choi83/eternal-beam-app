import { configuredExhibitionLocation } from './exhibition-passport-api.ts'
import {
  buildPassportSvg,
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

async function imageDataUrl(
  url: string,
  fetchImpl: typeof fetch,
): Promise<string> {
  let response: Response
  try {
    response = await fetchImpl(url, { cache: 'no-store' })
  } catch {
    throw new ExhibitionPassportDownloadError()
  }
  if (!response.ok) throw new ExhibitionPassportDownloadError()

  try {
    const blob = await response.blob()
    return `data:${blob.type || 'image/png'};base64,${base64(new Uint8Array(await blob.arrayBuffer()))}`
  } catch {
    throw new ExhibitionPassportDownloadError()
  }
}

/** Download the exact same template-based SVG composition shown on screen. */
export async function downloadPetPassport(
  record: PassportRecord,
  fetchImpl: typeof fetch = fetch,
  documentImpl?: Document,
  language: 'en' | 'ko' = 'en',
): Promise<void> {
  if (!record.photo_url) throw new ExhibitionPassportDownloadError()
  const doc = documentImpl || (typeof document !== 'undefined' ? document : null)
  if (!doc) throw new ExhibitionPassportDownloadError()

  const templateData = await imageDataUrl(PASSPORT_TEMPLATE_PATH, fetchImpl)
  const photoData = await imageDataUrl(record.photo_url, fetchImpl)
  const location = passportLocationLabel(record.exhibition_id, configuredExhibitionLocation())
  const passportSvg = buildPassportSvg(record, {
    templateHref: templateData,
    photoHref: photoData,
    location,
    language,
  })

  const objectUrl = URL.createObjectURL(new Blob([passportSvg], { type: 'image/svg+xml' }))
  const anchor = doc.createElement('a')
  anchor.href = objectUrl
  anchor.download = `pet-passport-${record.queue_number}.svg`
  anchor.rel = 'noopener'
  doc.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(objectUrl)
}
