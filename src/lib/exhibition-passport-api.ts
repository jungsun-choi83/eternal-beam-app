import { currentExhibitionId } from './exhibition-queue.ts'

export const EXHIBITION_PASSPORT_PATH = '/exhibition/passport'
export const EXHIBITION_PASSPORT_VIEW_PATH = '/exhibition/passport/view'
export const EXHIBITION_PASSPORT_REGISTRATION_PATH = '/api/exhibition/passport/registrations'
export const EXHIBITION_PASSPORT_RETRIEVE_PATH = '/api/exhibition/passport/retrieve'
export const EXHIBITION_PASSPORT_PRIVACY_POLICY_VERSION = 'v1'

export type PassportPhotoStatus = 'WAITING_FOR_PHOTO' | 'PHOTO_RECEIVED'

export interface PassportRecord {
  exhibition_id: string
  queue_number: number
  pet_name: string
  photo_status: PassportPhotoStatus
  photo_received_at: string | null
  created_at: string | null
  /** Short-lived URL issued only after token-scoped retrieval. */
  photo_url?: string | null
  /** Generic, non-sensitive storage availability status. */
  photo_access_error?: 'PHOTO_UNAVAILABLE' | null
}

export interface RegistrationResponse extends PassportRecord {
  /** The same client-generated opaque token used as the idempotency key. */
  passport_token: string
  replayed: boolean
}

export interface RegisterPetPassportParams {
  exhibitionId: string | null
  petName: string
  email: string
  privacyConsent: boolean
  passportToken: string
  privacyPolicyVersion?: string
}

type FetchFn = typeof fetch

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL
    return (raw || '').trim().replace(/\/$/, '')
  } catch {
    return ''
  }
}

function featureFlag(name: string): string {
  try {
    return String((import.meta as { env?: Record<string, string> }).env?.[name] || '').trim().toLowerCase()
  } catch {
    return ''
  }
}

/** Frontend opt-in. The backend has a separate default-off flag. */
export function isExhibitionPassportEnabled(): boolean {
  return featureFlag('VITE_ENABLE_EXHIBITION_PASSPORT') === '1' || featureFlag('VITE_ENABLE_EXHIBITION_PASSPORT') === 'true'
}

export function isExhibitionPassportLocalPreview(): boolean {
  return featureFlag('VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW') === '1' || featureFlag('VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW') === 'true'
}

/** Optional event display configuration; never inferred from the exhibition ID. */
export function configuredExhibitionLocation(): string | null {
  try {
    const value = String((import.meta as { env?: Record<string, string> }).env?.VITE_EXHIBITION_LOCATION || '').trim()
    return value || null
  } catch {
    return null
  }
}

export function isExhibitionPassportPath(pathname: string): boolean {
  return ((pathname || '').replace(/\/+$/, '') || '/') === EXHIBITION_PASSPORT_PATH
}

export function isExhibitionPassportViewPath(pathname: string): boolean {
  return ((pathname || '').replace(/\/+$/, '') || '/') === EXHIBITION_PASSPORT_VIEW_PATH
}

export function isExhibitionPassportEntry(): boolean {
  if (typeof window === 'undefined') return false
  return isExhibitionPassportPath(window.location.pathname)
}

export function isExhibitionPassportViewEntry(): boolean {
  if (typeof window === 'undefined') return false
  return isExhibitionPassportViewPath(window.location.pathname)
}

export function exhibitionPassportExhibitionId(): string | null {
  return currentExhibitionId()
}

export class ExhibitionPassportApiError extends Error {
  readonly code: string
  readonly status: number

  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ExhibitionPassportApiError'
    this.code = code
    this.status = status
  }
}

async function readError(response: Response): Promise<ExhibitionPassportApiError> {
  let code = 'UNKNOWN'
  let message = `HTTP ${response.status}`
  try {
    const body = (await response.json()) as { detail?: { code?: string; message?: string } }
    if (body?.detail?.code) code = body.detail.code
    if (body?.detail?.message) message = body.detail.message
  } catch {
    // Keep the generic status message when the server did not return JSON.
  }
  return new ExhibitionPassportApiError(code, message, response.status)
}

function validateToken(token: string): void {
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(token)) {
    throw new ExhibitionPassportApiError('INVALID_REGISTRATION_TOKEN', 'The registration token is invalid.', 400)
  }
}

/** Generate the Step 1 contract's URL-safe, 256-bit bearer/idempotency token. */
export function createPassportToken(cryptoImpl: Crypto = globalThis.crypto): string {
  const bytes = new Uint8Array(32)
  cryptoImpl.getRandomValues(bytes)
  const binary = Array.from(bytes, (byte) => String.fromCharCode(byte)).join('')
  const encoded = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '')
  validateToken(encoded)
  return encoded
}

export async function registerPetPassport(
  params: RegisterPetPassportParams,
  fetchImpl: FetchFn = fetch,
): Promise<RegistrationResponse> {
  validateToken(params.passportToken)
  const response = await fetchImpl(`${apiBase()}${EXHIBITION_PASSPORT_REGISTRATION_PATH}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      exhibition_id: params.exhibitionId,
      pet_name: params.petName,
      email: params.email,
      privacy_consent: params.privacyConsent,
      idempotency_key: params.passportToken,
      privacy_policy_version: params.privacyPolicyVersion || EXHIBITION_PASSPORT_PRIVACY_POLICY_VERSION,
    }),
  })
  if (!response.ok) throw await readError(response)
  return (await response.json()) as RegistrationResponse
}

export async function retrievePetPassport(
  passportToken: string,
  fetchImpl: FetchFn = fetch,
): Promise<PassportRecord> {
  validateToken(passportToken)
  const response = await fetchImpl(`${apiBase()}${EXHIBITION_PASSPORT_RETRIEVE_PATH}`, {
    method: 'POST',
    cache: 'no-store',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ passport_token: passportToken }),
  })
  if (!response.ok) throw await readError(response)
  return (await response.json()) as PassportRecord
}
