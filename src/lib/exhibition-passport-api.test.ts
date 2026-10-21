import assert from 'node:assert/strict'
import test from 'node:test'
import {
  createPassportToken,
  isExhibitionPassportEnabled,
  isExhibitionPassportPath,
  registerPetPassport,
  retrievePetPassport,
  type RegistrationResponse,
} from './exhibition-passport-api.ts'

const token = 'A'.repeat(43)

test('the public route is exact, while the frontend submit feature is opt-in by default', () => {
  assert.equal(isExhibitionPassportPath('/exhibition/passport'), true)
  assert.equal(isExhibitionPassportPath('/exhibition/passport/'), true)
  assert.equal(isExhibitionPassportPath('/exhibition/staff'), false)
  assert.equal(isExhibitionPassportEnabled(), false)
})

test('Passport token generation uses 256 bits and the server-safe URL alphabet', () => {
  const generated = createPassportToken({
    getRandomValues(bytes: Uint8Array) {
      bytes.fill(7)
      return bytes
    },
  } as Crypto)
  assert.equal(generated.length, 43)
  assert.match(generated, /^[A-Za-z0-9_-]+$/)
})

test('registration client sends the documented body and no photo fields', async () => {
  let request: RequestInit | undefined
  const response: RegistrationResponse = {
    exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
    photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: '2026-10-19T00:00:00Z',
    passport_token: token, replayed: false,
  }
  const fetchMock = async (_input: RequestInfo | URL, init?: RequestInit) => {
    request = init
    return new Response(JSON.stringify(response), { status: 200 })
  }
  const result = await registerPetPassport({
    exhibitionId: 'seoul-2026', petName: 'Momo', email: 'visitor@example.com',
    privacyConsent: true, passportToken: token,
  }, fetchMock)
  assert.deepEqual(result, response)
  assert.equal(request?.method, 'POST')
  assert.equal((request?.headers as Record<string, string>)['Content-Type'], 'application/json')
  assert.deepEqual(JSON.parse(String(request?.body)), {
    exhibition_id: 'seoul-2026', pet_name: 'Momo', email: 'visitor@example.com',
    privacy_consent: true, idempotency_key: token, privacy_policy_version: 'v1',
  })
  assert.equal(String(request?.body).includes('photo'), false)
})

test('retrieval sends the opaque token in a POST body, never a URL', async () => {
  let input: RequestInfo | URL = ''
  let request: RequestInit | undefined
  const fetchMock = async (received: RequestInfo | URL, init?: RequestInit) => {
    input = received
    request = init
    return new Response(JSON.stringify({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    }), { status: 200 })
  }
  await retrievePetPassport(token, fetchMock)
  assert.equal(String(input), '/api/exhibition/passport/retrieve')
  assert.deepEqual(JSON.parse(String(request?.body)), { passport_token: token })
  assert.equal(String(input).includes(token), false)
})

test('retrieval preserves the token-scoped short-lived photo URL without adding visitor data', async () => {
  const result = await retrievePetPassport(token, async () => new Response(JSON.stringify({
    exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
    photo_status: 'PHOTO_RECEIVED', photo_received_at: '2026-10-19T00:00:00Z', created_at: null,
    photo_url: 'https://storage.example/signed-source.png', photo_access_error: null,
  }), { status: 200 }))
  assert.equal(result.photo_url, 'https://storage.example/signed-source.png')
  assert.equal(result.photo_access_error, null)
  assert.equal('email' in result, false)
})
