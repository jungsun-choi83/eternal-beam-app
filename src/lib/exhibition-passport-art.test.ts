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
  assert.match(svg, /Eternal%20Beam%20Travel%20Passport%20Duo%20Clean\.png/)
  assert.match(svg, /P&lt;EBBMOMO/)
})

test('places the live photo under a foreground copy of the baked frame', () => {
  const svg = buildPassportSvg({
    exhibition_id: 'seoul-2026',
    queue_number: 42,
    pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED',
    photo_received_at: null,
    created_at: '2026-10-20T00:00:00Z',
  }, { photoHref: 'data:image/png;base64,photo' })
  assert.match(svg, /id="passportPhotoFrameMask"/)
  assert.match(svg, /mask="url\(#passportPhotoFrameMask\)"/)
  assert.match(svg, /id="passportPhotoClip"><path d="M 196 312/)
  assert.match(svg, /<path d="M 196 312[^>]+fill="black"\/>/)
  assert.match(svg, /id="passport-photo"[^>]+transform="rotate\(1\.5 295 453\.5\)"[^>]+preserveAspectRatio="xMidYMid slice"/)
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
