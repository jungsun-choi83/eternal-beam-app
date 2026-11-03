import assert from 'node:assert/strict'
import test from 'node:test'
import {
  exhibitionPassportSessionKey,
  readPassportSession,
  savePassportSession,
  readPendingPassportRegistration,
  savePendingPassportRegistration,
  type StorageLike,
} from './exhibition-passport-session.ts'
import { shouldStartRegistration } from './exhibition-passport-flow.ts'

function storage(): StorageLike {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => void values.set(key, value),
    removeItem: (key) => void values.delete(key),
  }
}

test('a stored Passport session prevents a refresh from starting another registration', () => {
  const fakeStorage = storage()
  const record = {
    exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
    photo_status: 'WAITING_FOR_PHOTO' as const, photo_received_at: null, created_at: null,
  }
  savePassportSession(record, 'B'.repeat(43), fakeStorage)
  assert.equal(readPassportSession(fakeStorage, 'seoul-2026')?.queueNumber, 42)
  assert.equal(readPassportSession(fakeStorage)?.queueNumber, 42)
  assert.equal(shouldStartRegistration(true), false)
  assert.equal(shouldStartRegistration(false), true)
})

test('invalid or incomplete storage data is ignored', () => {
  const fakeStorage = storage()
  fakeStorage.setItem(exhibitionPassportSessionKey(null), JSON.stringify({ passportToken: 'short' }))
  assert.equal(readPassportSession(fakeStorage), null)
})

test('a pending registration keeps the same token for a response-loss retry', () => {
  const fakeStorage = storage()
  savePendingPassportRegistration({
    passportToken: 'C'.repeat(43), exhibitionId: 'seoul-2026', petName: 'Momo',
    email: 'visitor@example.com', privacyConsent: true, privacyPolicyVersion: 'v1',
  }, fakeStorage)
  assert.equal(readPendingPassportRegistration(fakeStorage, 'seoul-2026')?.passportToken, 'C'.repeat(43))
})
