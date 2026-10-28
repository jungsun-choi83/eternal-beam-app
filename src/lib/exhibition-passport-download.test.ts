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

test('embeds both the approved template and the source photo in the downloaded SVG', async () => {
  let downloaded: Blob | null = null
  const originalCreateObjectUrl = URL.createObjectURL
  URL.createObjectURL = ((value: Blob) => {
    downloaded = value
    return 'blob:passport-test'
  }) as typeof URL.createObjectURL

  const anchor = {
    href: '',
    download: '',
    rel: '',
    click() {},
    remove() {},
  }
  const documentMock = {
    body: { appendChild() {} },
    createElement() { return anchor },
  } as unknown as Document

  try {
    await downloadPetPassport(
      {
        exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
        photo_status: 'PHOTO_RECEIVED', photo_received_at: null, created_at: '2026-10-20T00:00:00Z',
        photo_url: 'https://storage.example/signed-source.png',
      },
      async () => new Response(new Blob(['image']), { status: 200, headers: { 'content-type': 'image/png' } }),
      documentMock,
    )
    const svg = await downloaded?.text()
    assert.match(svg || '', /viewBox="0 0 1536 1024"/)
    assert.equal((svg?.match(/data:image\/png;base64/g) || []).length, 3)
  } finally {
    URL.createObjectURL = originalCreateObjectUrl
  }
})
