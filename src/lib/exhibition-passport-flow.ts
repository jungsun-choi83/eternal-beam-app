import type { PassportRecord } from './exhibition-passport-api'

export type PassportStage = 'register' | 'registered' | 'waiting' | 'ready'

export function passportStage(record: PassportRecord | null): PassportStage {
  if (!record) return 'register'
  return record.photo_status === 'PHOTO_RECEIVED' ? 'ready' : 'waiting'
}

export function hasPassportPhoto(record: PassportRecord | null): boolean {
  return record?.photo_status === 'PHOTO_RECEIVED'
}

/** A stored token is the retry/idempotency boundary: refreshes must not register again. */
export function shouldStartRegistration(hasStoredSession: boolean): boolean {
  return !hasStoredSession
}
