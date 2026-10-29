// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const reservePetPassportId = vi.fn()
const retrievePetPassport = vi.fn()
const savePetPassportEmail = vi.fn()

vi.mock('@/lib/exhibition-passport-api', () => ({
  createPassportToken: () => 'C'.repeat(43),
  configuredExhibitionLocation: () => null,
  exhibitionPassportExhibitionId: () => 'seoul-2026',
  isExhibitionPassportEnabled: () => true,
  isExhibitionPassportLocalPreview: () => String(import.meta.env.VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW || '') === '1',
  reservePetPassportId: (...args: unknown[]) => reservePetPassportId(...args),
  retrievePetPassport: (...args: unknown[]) => retrievePetPassport(...args),
  savePetPassportEmail: (...args: unknown[]) => savePetPassportEmail(...args),
}))

import { ExhibitionPassportScreen } from './exhibition-passport-screen'

afterEach(() => {
  cleanup()
  localStorage.clear()
  window.history.replaceState({}, '', '/')
  reservePetPassportId.mockReset()
  retrievePetPassport.mockReset()
  savePetPassportEmail.mockReset()
  vi.unstubAllEnvs()
})

beforeEach(() => {
  vi.stubEnv('DEV', false)
})

describe('ExhibitionPassportScreen', () => {
  it('renders the approved Pet Passport homepage without registration fields', () => {
    render(<ExhibitionPassportScreen />)
    expect(screen.getByRole('heading', { name: 'PET PASSPORT' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Create my Pet's ID/i })).toBeTruthy()
    expect(screen.queryByRole('img', { name: 'Eternal Beam' })).toBeNull()
    expect(screen.queryByText('A JOURNEY OF LOVE BEYOND TIME')).toBeNull()
    expect(screen.queryByText('Give your pet an identity.')).toBeNull()
    expect(screen.queryByText('How it works')).toBeNull()
    expect(screen.queryByText('Get Photo at Booth')).toBeNull()
    expect(screen.queryByText('Download Passport')).toBeNull()
    expect(screen.queryByLabelText('Pet name')).toBeNull()
    expect(screen.queryByLabelText('Email address')).toBeNull()
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })

  it('creates the server-issued Pet ID directly from the homepage CTA without a form', async () => {
    reservePetPassportId.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      passport_token: 'C'.repeat(43), replayed: false,
    })
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    })
    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Create my Pet's ID/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy())
    expect(reservePetPassportId).toHaveBeenCalledWith({ exhibitionId: 'seoul-2026', passportToken: 'C'.repeat(43) })
    expect(screen.getByText('#042')).toBeTruthy()
    expect(screen.queryByText('Photo in progress')).toBeNull()
    expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy()
    expect(screen.getByText('Visit the Photo Booth')).toBeTruthy()
    expect(screen.getByText('See Your Pet at Eternal Beam')).toBeTruthy()
    expect(screen.getByText('Visit Soul Trace')).toBeTruthy()
    expect(screen.getByText('Claim Your Gift')).toBeTruthy()
    expect(screen.getByText("Download Your Pet's Passport")).toBeTruthy()
    expect(document.getElementById('passport-email-panel')?.getAttribute('aria-hidden')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: 'Show Eternal Beam email options' }))
    expect(document.getElementById('passport-email-panel')?.getAttribute('aria-hidden')).toBe('false')
    expect(screen.getByLabelText('Enter your email (optional)')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Preview the Pet's Passport/i })).toBeTruthy()
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    const photoBoothProgress = screen.getByRole('button', { name: 'Mark Visit the Photo Booth as complete' })
    fireEvent.click(photoBoothProgress)
    expect(photoBoothProgress.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(photoBoothProgress)
    expect(photoBoothProgress.getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByRole('heading', { name: /Get your Eternal Beam video/i })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Copy ID/i })).toBeNull()
    expect(screen.getByLabelText("Your pet's name (Optional)")).toBeTruthy()
    expect(screen.queryByLabelText('Email address')).toBeNull()
    expect(screen.queryByText('Register your pet')).toBeNull()
  })

  it('uses the isolated local backend when local preview mode is enabled', async () => {
    vi.stubEnv('DEV', true)
    vi.stubEnv('VITE_ENABLE_EXHIBITION_LOCAL_PREVIEW', '1')
    reservePetPassportId.mockResolvedValue({
      exhibition_id: 'local-preview', queue_number: 1, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      passport_token: 'C'.repeat(43), replayed: false,
    })
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'local-preview', queue_number: 1, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    })
    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Create my Pet's ID/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy())
    expect(screen.getByText('#001')).toBeTruthy()
    expect(reservePetPassportId).toHaveBeenCalledWith({ exhibitionId: 'seoul-2026', passportToken: 'C'.repeat(43) })
  })

  it('saves the optional email through the existing Passport token without creating another ID', async () => {
    reservePetPassportId.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      passport_token: 'C'.repeat(43), replayed: false,
    })
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      email_saved: false, ai_video_consent: false, marketing_consent: false,
    })
    savePetPassportEmail.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      email_saved: true, ai_video_consent: true, marketing_consent: false,
    })

    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Create my Pet's ID/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Show Eternal Beam email options' }))
    fireEvent.change(screen.getByLabelText(/Enter your email/i), { target: { value: 'visitor@example.com' } })
    fireEvent.click(screen.getByLabelText(/Yes, email me when my Eternal Beam AI video/i))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('Your email preference was saved securely.'))
    expect(savePetPassportEmail).toHaveBeenCalledWith({
      passportToken: 'C'.repeat(43),
      email: 'visitor@example.com',
      aiVideoConsent: true,
      marketingConsent: false,
    })
    expect(reservePetPassportId).toHaveBeenCalledTimes(1)
  })

  it('switches the approved homepage copy between English and Korean', () => {
    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Switch language to Korean/i }))
    expect(screen.getByRole('heading', { name: 'PET PASSPORT' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Pet ID/i })).toBeTruthy()
    expect(screen.queryByText('시간을 넘어 이어지는 사랑의 여정')).toBeNull()
    expect(screen.queryByText('이용 방법')).toBeNull()
    expect(screen.getByRole('button', { name: /Switch language to English/i })).toBeTruthy()
  })

  it('displays the server-issued queue number and waiting state after ID reservation', async () => {
    reservePetPassportId.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      passport_token: 'C'.repeat(43), replayed: false,
    })
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: null,
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    })
    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Create my Pet's ID/i }))
    await waitFor(() => expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy())
    expect(screen.getByText('#042')).toBeTruthy()
    expect(screen.queryByText('Photo in progress')).toBeNull()
    expect(screen.queryByRole('button', { name: /Copy ID/i })).toBeNull()
    expect(screen.queryByRole('link', { name: /Discover Soul Trace/i })).toBeNull()
    expect(reservePetPassportId).toHaveBeenCalledTimes(1)
    expect(reservePetPassportId.mock.calls[0][0].passportToken).toHaveLength(43)
  })

  it('restores the exhibition-photo card from the persisted session', async () => {
    localStorage.setItem('eternal_beam_exhibition_passport_v1:seoul-2026', JSON.stringify({
      passportToken: 'C'.repeat(43), exhibitionId: 'seoul-2026', queueNumber: 42,
      petName: 'Momo', photoStatus: 'WAITING_FOR_PHOTO', createdAt: null,
    }))
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    })
    render(<ExhibitionPassportScreen />)
    expect(await screen.findByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy()
    expect(screen.getByText('#042')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Copy ID/i })).toBeNull()
  })

  it('shows a persisted photo on Step 3 before continuing to the final Passport', async () => {
    localStorage.setItem('eternal_beam_exhibition_passport_v1:seoul-2026', JSON.stringify({
      passportToken: 'C'.repeat(43), exhibitionId: 'seoul-2026', queueNumber: 42,
      petName: 'Momo', photoStatus: 'PHOTO_RECEIVED', createdAt: null,
    }))
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED', photo_received_at: '2026-10-19T00:00:00Z', created_at: '2026-10-20T00:00:00Z',
      photo_url: 'https://storage.example/signed-source.png', photo_access_error: null,
    })

    render(<ExhibitionPassportScreen />)
    expect(screen.queryByText('Photo added')).toBeNull()
    await waitFor(() => expect(document.querySelector('.passport-photo-frame img')).toBeTruthy())
    expect(screen.getByRole('heading', { name: 'HOW IT WORKS' })).toBeTruthy()
    expect((screen.getByLabelText("Your pet's name (Optional)") as HTMLInputElement).value).toBe('Momo')
  })

  it('shows an unavailable-photo state without a download action when storage access is missing', async () => {
    localStorage.setItem('eternal_beam_exhibition_passport_v1:seoul-2026', JSON.stringify({
      passportToken: 'C'.repeat(43), exhibitionId: 'seoul-2026', queueNumber: 42,
      petName: 'Momo', photoStatus: 'PHOTO_RECEIVED', createdAt: null,
    }))
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED', photo_received_at: null, created_at: null,
      photo_url: null, photo_access_error: 'PHOTO_UNAVAILABLE',
    })

    render(<ExhibitionPassportScreen />)
    expect(screen.queryByText(/temporarily unavailable/i)).toBeNull()
    expect(screen.queryByRole('button', { name: /Download Passport/i })).toBeNull()
  })
})
