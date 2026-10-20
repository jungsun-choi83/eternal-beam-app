import assert from 'node:assert/strict'
import test from 'node:test'
import { downloadPetPassport } from './exhibition-passport-download.ts'

test('downloads a self-contained Passport image with a non-sensitive filename', async () => {
  let clicked = false
  let appended = false
  const anchor = {
    href: '',
    download: '',
    rel: '',
    click() { clicked = true },
    remove() { appended = false },
  }
  const documentMock = {
    body: { appendChild() { appended = true } },
    createElement() { return anchor },
  } as unknown as Document

  await downloadPetPassport(
    {
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED', photo_received_at: null, created_at: null,
      photo_url: 'https://storage.example/signed-source.png',
    },
    async () => new Response(new Blob(['png']), { status: 200, headers: { 'content-type': 'image/png' } }),
    documentMock,
  )

  assert.equal(clicked, true)
  assert.equal(appended, false)
  assert.equal(anchor.download, 'pet-passport-42.svg')
  assert.match(anchor.href, /^blob:/)
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
