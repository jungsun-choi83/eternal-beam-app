import assert from 'node:assert/strict'
import test from 'node:test'
import { buildPassportSvg, passportLocationLabel } from './exhibition-passport-art.ts'

test('uses Seoul, Korea as the Passport location fallback', () => {
  assert.equal(passportLocationLabel('seoul-2026'), 'Seoul, Korea')
  const svg = buildPassportSvg({
    exhibition_id: 'seoul-2026',
    queue_number: 42,
    pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED',
    photo_received_at: null,
    created_at: '2026-10-20T00:00:00Z',
  })
  assert.match(svg, /passport%20%282%29\.png/)
  assert.match(svg, />2026\.10\.20<\/text>/)
})

test('places the live photo inside the lower-page photo opening', () => {
  const svg = buildPassportSvg({
    exhibition_id: 'seoul-2026',
    queue_number: 42,
    pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED',
    photo_received_at: null,
    created_at: '2026-10-20T00:00:00Z',
  }, { photoHref: 'data:image/png;base64,photo' })
  assert.match(svg, /id="passportPhotoClip"><path d="M 176 885 H 472 V 1220 H 176 Z"\/><\/clipPath>/)
  assert.match(svg, /id="passport-photo"[^>]+preserveAspectRatio="xMidYMid slice"/)
  assert.doesNotMatch(svg, /passportPhotoFrameMask|rotate\(/)
})

test('keeps an explicitly configured location when one is provided', () => {
  assert.equal(passportLocationLabel('seoul-2026', 'Busan, Korea'), 'Busan, Korea')
  assert.match(buildPassportSvg({
    exhibition_id: 'seoul-2026',
    queue_number: 42,
    pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED',
    photo_received_at: null,
    created_at: '2026-10-20T00:00:00Z',
  }, { location: 'Busan, Korea' }), />Busan, Korea<\/text>/)
})
