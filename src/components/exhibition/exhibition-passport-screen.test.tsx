// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const registerPetPassport = vi.fn()
const retrievePetPassport = vi.fn()

vi.mock('@/lib/exhibition-passport-api', () => ({
  createPassportToken: () => 'C'.repeat(43),
  configuredExhibitionLocation: () => null,
  exhibitionPassportExhibitionId: () => 'seoul-2026',
  isExhibitionPassportEnabled: () => true,
  isExhibitionPassportLocalPreview: () => false,
  registerPetPassport: (...args: unknown[]) => registerPetPassport(...args),
  retrievePetPassport: (...args: unknown[]) => retrievePetPassport(...args),
}))

import { ExhibitionPassportScreen } from './exhibition-passport-screen'

afterEach(() => {
  cleanup()
  localStorage.clear()
  registerPetPassport.mockReset()
  retrievePetPassport.mockReset()
})

describe('ExhibitionPassportScreen', () => {
  it('renders the journey, registration fields, and no visitor photo upload', () => {
    render(<ExhibitionPassportScreen />)
    expect(screen.getByRole('button', { name: /Start Registration/i })).toBeTruthy()
    expect(screen.getByText('Get Your Pet ID')).toBeTruthy()
    expect(screen.getByText('Visit the Photo Booth')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Start Registration/i }))
    expect(screen.getAllByText('Register your pet').length).toBeGreaterThan(0)
    expect(screen.getByLabelText('Pet name')).toBeTruthy()
    expect(screen.getByLabelText('Email address')).toBeTruthy()
    expect(document.querySelector('input[type="file"]')).toBeNull()
  })

  it('displays the server-issued queue number and waiting state after registration', async () => {
    registerPetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
      passport_token: 'C'.repeat(43), replayed: false,
    })
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
      photo_status: 'WAITING_FOR_PHOTO', photo_received_at: null, created_at: null,
    })
    render(<ExhibitionPassportScreen />)
    fireEvent.click(screen.getByRole('button', { name: /Start Registration/i }))
    fireEvent.change(screen.getByLabelText('Pet name'), { target: { value: 'Momo' } })
    fireEvent.change(screen.getByLabelText('Email address'), { target: { value: 'visitor@example.com' } })
    fireEvent.click(screen.getByRole('checkbox'))
    fireEvent.click(screen.getByRole('button', { name: /Create my Pet ID/i }))
    await waitFor(() => expect(screen.getByText('#042')).toBeTruthy())
    expect(screen.getByText('Waiting for staff photo')).toBeTruthy()
    expect(screen.queryByRole('link', { name: /Discover Soul Trace/i })).toBeNull()
    expect(registerPetPassport).toHaveBeenCalledTimes(1)
    expect(registerPetPassport.mock.calls[0][0].passportToken).toHaveLength(43)
  })

  it('keeps the completed Passport on its dedicated page', async () => {
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
    expect(await screen.findByText('Your Passport is ready')).toBeTruthy()
    expect(screen.getByRole('link', { name: /View My Passport/i }).getAttribute('href')).toBe('/exhibition/passport/view')
    expect(screen.queryByTestId('passport-art')).toBeNull()
    expect(screen.queryByRole('button', { name: /Download Passport/i })).toBeNull()
    expect(screen.getByText('Momo')).toBeTruthy()
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
    expect((await screen.findAllByText(/temporarily unavailable/i)).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /Download Passport/i })).toBeNull()
  })
})
