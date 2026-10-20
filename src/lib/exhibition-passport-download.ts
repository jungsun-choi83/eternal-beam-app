import type { PassportRecord } from './exhibition-passport-api'

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

function escapeXml(value: string): string {
  return value.replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&apos;', '"': '&quot;',
  })[character] as string)
}

/** Download a self-contained Passport image containing the real source photo. */
export async function downloadPetPassport(
  record: PassportRecord,
  fetchImpl: typeof fetch = fetch,
  documentImpl?: Document,
): Promise<void> {
  if (!record.photo_url) throw new ExhibitionPassportDownloadError()
  const doc = documentImpl || (typeof document !== 'undefined' ? document : null)
  if (!doc) throw new ExhibitionPassportDownloadError()
  let response: Response
  try {
    response = await fetchImpl(record.photo_url, { cache: 'no-store' })
  } catch {
    throw new ExhibitionPassportDownloadError()
  }
  if (!response.ok) throw new ExhibitionPassportDownloadError()

  let blob: Blob
  try {
    blob = await response.blob()
  } catch {
    throw new ExhibitionPassportDownloadError()
  }

  let photoData: string
  try {
    photoData = `data:${blob.type || 'image/png'};base64,${base64(new Uint8Array(await blob.arrayBuffer()))}`
  } catch {
    throw new ExhibitionPassportDownloadError()
  }

  const passportSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1500" viewBox="0 0 1200 1500">
  <rect width="1200" height="1500" fill="#090908"/>
  <rect x="34" y="34" width="1132" height="1432" rx="10" fill="none" stroke="#d8b46a" stroke-width="3"/>
  <text x="90" y="112" fill="#d8b46a" font-family="Georgia,serif" font-size="30" letter-spacing="8">ETERNAL BEAM</text>
  <text x="90" y="160" fill="#f7f0df" font-family="Arial,sans-serif" font-size="18" letter-spacing="4">SOUL TRACE / PET PASSPORT</text>
  <rect x="90" y="220" width="1020" height="870" rx="6" fill="#171613"/>
  <image href="${photoData}" x="90" y="220" width="1020" height="870" preserveAspectRatio="xMidYMid slice"/>
  <rect x="90" y="220" width="1020" height="870" rx="6" fill="none" stroke="#d8b46a" stroke-opacity=".65" stroke-width="2"/>
  <text x="90" y="1190" fill="#9b9588" font-family="Arial,sans-serif" font-size="18" letter-spacing="3">COMPANION</text>
  <text x="90" y="1250" fill="#f7f0df" font-family="Georgia,serif" font-size="48">${escapeXml(record.pet_name)}</text>
  <text x="90" y="1332" fill="#9b9588" font-family="Arial,sans-serif" font-size="18" letter-spacing="3">PET ID / QUEUE NUMBER</text>
  <text x="90" y="1392" fill="#d8b46a" font-family="Arial,sans-serif" font-size="38" letter-spacing="4">#${record.queue_number}</text>
</svg>`

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
