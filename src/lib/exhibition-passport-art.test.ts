import assert from 'node:assert/strict'
import test from 'node:test'
import { buildPassportSvg, passportLocationLabel } from './exhibition-passport-art.ts'

test('uses Seoul, Korea as the Passport location fallback', () => {
  assert.equal(passportLocationLabel('seoul-2026'), 'Seoul, Korea')
  assert.match(buildPassportSvg({
    exhibition_id: 'seoul-2026',
    queue_number: 42,
    pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED',
    photo_received_at: null,
    created_at: '2026-10-20T00:00:00Z',
  }), />Seoul, Korea<\/text>/)
})

test('keeps an explicitly configured location when one is provided', () => {
  assert.equal(passportLocationLabel('seoul-2026', 'Busan, Korea'), 'Busan, Korea')
})
