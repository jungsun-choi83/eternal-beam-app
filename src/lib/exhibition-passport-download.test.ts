import assert from 'node:assert/strict'
import test from 'node:test'
import { formatPassportIssuedDateSeoul } from './exhibition-passport-art.ts'
import { downloadPetPassport } from './exhibition-passport-download.ts'

test('formats the issue date in the required Seoul calendar format', () => {
  assert.equal(formatPassportIssuedDateSeoul('2026-10-26T16:00:00.000Z'), '2026.10.27')
})

test('does not attempt a download when the signed photo URL is absent', async () => {
  await assert.rejects(
    () => downloadPetPassport({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED', photo_received_at: null, created_at: null,
      photo_url: null,
    }),
    /could not be downloaded/i,
  )
})
