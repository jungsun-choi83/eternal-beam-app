import type { PassportRecord } from './exhibition-passport-api'

export const EXHIBITION_PASSPORT_SESSION_KEY = 'eternal_beam_exhibition_passport_v1'
export const EXHIBITION_PASSPORT_PENDING_KEY = 'eternal_beam_exhibition_passport_pending_v1'
export const EXHIBITION_PASSPORT_ID_RESERVATION_PENDING_KEY = 'eternal_beam_exhibition_passport_id_pending_v1'
const EXHIBITION_PASSPORT_LAST_EXHIBITION_KEY = `${EXHIBITION_PASSPORT_SESSION_KEY}:last`

export function exhibitionPassportSessionKey(exhibitionId: string | null): string {
  return `${EXHIBITION_PASSPORT_SESSION_KEY}:${encodeURIComponent(exhibitionId || 'default')}`
}

function pendingRegistrationKey(exhibitionId: string | null): string {
  return `${EXHIBITION_PASSPORT_PENDING_KEY}:${encodeURIComponent(exhibitionId || 'default')}`
}

function pendingIdReservationKey(exhibitionId: string | null): string {
  return `${EXHIBITION_PASSPORT_ID_RESERVATION_PENDING_KEY}:${encodeURIComponent(exhibitionId || 'default')}`
}

export interface PassportSession {
  passportToken: string
  exhibitionId: string
  queueNumber: number
  petName: string | null
  photoStatus: PassportRecord['photo_status']
  createdAt: string | null
  emailSaved?: boolean
  aiVideoConsent?: boolean
  marketingConsent?: boolean
}

export interface PendingPassportRegistration {
  passportToken: string
  exhibitionId: string
  petName: string
  email: string
  privacyConsent: true
  privacyPolicyVersion: string
}

export interface PendingPassportIdReservation {
  passportToken: string
  exhibitionId: string
}

interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

function browserStorage(): StorageLike | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function validPassportToken(token: string): boolean {
  return /^[A-Za-z0-9_-]{43,128}$/.test(token)
}

function validSession(value: unknown): value is PassportSession {
  if (!value || typeof value !== 'object') return false
  const session = value as Partial<PassportSession>
  return (
    typeof session.passportToken === 'string' && validPassportToken(session.passportToken) &&
    typeof session.exhibitionId === 'string' && typeof session.queueNumber === 'number' &&
    Number.isInteger(session.queueNumber) && (session.petName === null || typeof session.petName === 'string') &&
    (session.photoStatus === 'WAITING_FOR_PHOTO' || session.photoStatus === 'PHOTO_RECEIVED') &&
    (session.createdAt === null || typeof session.createdAt === 'string') &&
    (session.emailSaved === undefined || typeof session.emailSaved === 'boolean') &&
    (session.aiVideoConsent === undefined || typeof session.aiVideoConsent === 'boolean') &&
    (session.marketingConsent === undefined || typeof session.marketingConsent === 'boolean')
  )
}

function validPendingRegistration(value: unknown): value is PendingPassportRegistration {
  if (!value || typeof value !== 'object') return false
  const pending = value as Partial<PendingPassportRegistration>
  return (
    typeof pending.passportToken === 'string' && /^[A-Za-z0-9_-]{43,128}$/.test(pending.passportToken) &&
    typeof pending.exhibitionId === 'string' && typeof pending.petName === 'string' &&
    typeof pending.email === 'string' && pending.privacyConsent === true &&
    typeof pending.privacyPolicyVersion === 'string'
  )
}

function validPendingIdReservation(value: unknown): value is PendingPassportIdReservation {
  if (!value || typeof value !== 'object') return false
  const pending = value as Partial<PendingPassportIdReservation>
  return (
    typeof pending.passportToken === 'string' && /^[A-Za-z0-9_-]{43,128}$/.test(pending.passportToken) &&
    typeof pending.exhibitionId === 'string'
  )
}

export function readPassportSession(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): PassportSession | null {
  if (!storage) return null
  try {
    const rememberedExhibition = exhibitionId || storage.getItem(EXHIBITION_PASSPORT_LAST_EXHIBITION_KEY)
    const raw = storage.getItem(exhibitionPassportSessionKey(rememberedExhibition))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return validSession(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function savePassportSession(
  record: PassportRecord,
  passportToken: string,
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = record.exhibition_id,
): void {
  if (!storage || !validPassportToken(passportToken)) return
  const existing = readPassportSession(storage, exhibitionId)
  const session: PassportSession = {
    passportToken,
    exhibitionId: record.exhibition_id,
    queueNumber: record.queue_number,
    petName: record.pet_name ?? existing?.petName ?? null,
    photoStatus: record.photo_status,
    createdAt: record.created_at,
    emailSaved: record.email_saved ?? existing?.emailSaved ?? false,
    aiVideoConsent: record.ai_video_consent ?? existing?.aiVideoConsent ?? false,
    marketingConsent: record.marketing_consent ?? existing?.marketingConsent ?? false,
  }
  try {
    storage.setItem(exhibitionPassportSessionKey(exhibitionId), JSON.stringify(session))
    storage.setItem(EXHIBITION_PASSPORT_LAST_EXHIBITION_KEY, exhibitionId || 'default')
  } catch {
    // A blocked/ full localStorage should not break the visitor flow.
  }
}

export function clearPassportSession(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): void {
  try {
    const rememberedExhibition = exhibitionId || storage?.getItem(EXHIBITION_PASSPORT_LAST_EXHIBITION_KEY) || null
    storage?.removeItem(exhibitionPassportSessionKey(rememberedExhibition))
    if (!exhibitionId || rememberedExhibition === exhibitionId) {
      storage?.removeItem(EXHIBITION_PASSPORT_LAST_EXHIBITION_KEY)
    }
  } catch {
    // Ignore storage cleanup failures.
  }
}

export function readPendingPassportRegistration(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): PendingPassportRegistration | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(pendingRegistrationKey(exhibitionId))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return validPendingRegistration(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function savePendingPassportRegistration(
  pending: PendingPassportRegistration,
  storage: StorageLike | null = browserStorage(),
): void {
  if (!storage || !/^[A-Za-z0-9_-]{43,128}$/.test(pending.passportToken)) return
  try {
    storage.setItem(pendingRegistrationKey(pending.exhibitionId), JSON.stringify(pending))
  } catch {
    // A blocked/ full localStorage should not break the visitor flow.
  }
}

export function readPendingPassportIdReservation(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): PendingPassportIdReservation | null {
  if (!storage) return null
  try {
    const raw = storage.getItem(pendingIdReservationKey(exhibitionId))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    return validPendingIdReservation(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function savePendingPassportIdReservation(
  pending: PendingPassportIdReservation,
  storage: StorageLike | null = browserStorage(),
): void {
  if (!storage || !/^[A-Za-z0-9_-]{43,128}$/.test(pending.passportToken)) return
  try {
    storage.setItem(pendingIdReservationKey(pending.exhibitionId), JSON.stringify(pending))
  } catch {
    // A blocked/full localStorage should not break the visitor flow.
  }
}

export function clearPendingPassportIdReservation(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): void {
  try {
    storage?.removeItem(pendingIdReservationKey(exhibitionId))
  } catch {
    // Ignore storage cleanup failures.
  }
}

export function clearPendingPassportRegistration(
  storage: StorageLike | null = browserStorage(),
  exhibitionId: string | null = null,
): void {
  try {
    storage?.removeItem(pendingRegistrationKey(exhibitionId))
  } catch {
    // Ignore storage cleanup failures.
  }
}

export type { StorageLike }
