// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const retrievePetPassport = vi.fn()
const downloadPetPassport = vi.fn()

vi.mock('@/lib/exhibition-passport-api', () => ({
  configuredExhibitionLocation: () => 'Seoul, Korea',
  exhibitionPassportExhibitionId: () => 'seoul-2026',
  retrievePetPassport: (...args: unknown[]) => retrievePetPassport(...args),
}))
vi.mock('@/lib/exhibition-passport-download', () => ({
  downloadPetPassport: (...args: unknown[]) => downloadPetPassport(...args),
}))

import { ExhibitionPassportViewScreen } from './exhibition-passport-view-screen'

const token = 'C'.repeat(43)

function saveSession(photoStatus: 'WAITING_FOR_PHOTO' | 'PHOTO_RECEIVED' = 'WAITING_FOR_PHOTO', exhibitionId = 'seoul-2026') {
  localStorage.setItem(`eternal_beam_exhibition_passport_v1:${exhibitionId}`, JSON.stringify({
    passportToken: token,
    exhibitionId,
    queueNumber: 42,
    petName: 'Momo',
    photoStatus,
    createdAt: '2026-10-20T00:00:00Z',
  }))
}

afterEach(() => {
  cleanup()
  localStorage.clear()
  retrievePetPassport.mockReset()
  downloadPetPassport.mockReset()
  window.history.replaceState({}, '', '/exhibition/passport/view')
})

describe('ExhibitionPassportViewScreen', () => {
  it('renders the shared Passport artwork and download action after secure retrieval', async () => {
    saveSession('PHOTO_RECEIVED')
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026',
      queue_number: 42,
      pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED',
      photo_received_at: '2026-10-20T00:00:00Z',
      created_at: '2026-10-20T00:00:00Z',
      photo_url: 'https://storage.example/signed-source.png',
      photo_access_error: null,
    })

    render(<ExhibitionPassportViewScreen />)

    expect(await screen.findByTestId('passport-art')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Your Pet Passport' })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Download Passport/i })).toBeTruthy()
    expect(screen.getByRole('link', { name: /Back to My Pet ID/i }).getAttribute('href')).toBe('/exhibition/passport')
    expect(screen.getByTestId('passport-art').innerHTML).toContain('Eternal%20Beam%20Travel%20Passport%20Duo%20Clean.png')
    expect(screen.getByAltText(/Discover Soul Trace promotional banner/i).getAttribute('src')).toBe('/home/soultrace(eng)-cropped.png')
    const promoCta = screen.getByRole('link', { name: 'Discover Soul Trace' })
    expect(promoCta.getAttribute('href')).toBe('https://soultrace.pet')
    expect(promoCta.getAttribute('target')).toBe('_blank')
    expect(promoCta.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByTestId('passport-soultrace-banner')).toBe(promoCta)

    fireEvent.click(screen.getByRole('button', { name: /Download Passport/i }))
    await waitFor(() => expect(downloadPetPassport).toHaveBeenCalledTimes(1))
    expect(retrievePetPassport).toHaveBeenCalledWith(token)
  })

  it('uses the Korean banner and accessible CTA when the locale is switched', async () => {
    saveSession('PHOTO_RECEIVED')
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026',
      queue_number: 42,
      pet_name: 'Momo',
      photo_status: 'PHOTO_RECEIVED',
      photo_received_at: '2026-10-20T00:00:00Z',
      created_at: '2026-10-20T00:00:00Z',
      photo_url: 'https://storage.example/signed-source.png',
    })

    render(<ExhibitionPassportViewScreen />)
    await screen.findByTestId('passport-art')
    fireEvent.click(screen.getByRole('button', { name: /Switch language to 한국어/i }))

    expect(screen.getByAltText('소울트레이스 소개 배너').getAttribute('src')).toBe('/home/soultrace(ko)-cropped.png')
    expect(screen.getByRole('link', { name: '소울트레이스 알아보기' }).getAttribute('href')).toBe('https://soultrace.pet')
  })

  it('shows a waiting state without rendering or downloading a Passport before the photo arrives', async () => {
    saveSession()
    retrievePetPassport.mockResolvedValue({
      exhibition_id: 'seoul-2026',
      queue_number: 42,
      pet_name: 'Momo',
      photo_status: 'WAITING_FOR_PHOTO',
      photo_received_at: null,
      created_at: '2026-10-20T00:00:00Z',
    })

    render(<ExhibitionPassportViewScreen />)

    expect(await screen.findByText('Your Passport is being prepared')).toBeTruthy()
    expect(screen.queryByTestId('passport-art')).toBeNull()
    expect(screen.queryByRole('button', { name: /Download Passport/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Refresh Status/i })).toBeNull()
  })

  it('shows a recoverable state when the secure Passport session has expired', async () => {
    saveSession()
    retrievePetPassport.mockRejectedValue({ status: 404 })

    render(<ExhibitionPassportViewScreen />)

    expect((await screen.findByRole('alert')).textContent).toMatch(/private Passport session is not available/i)
    expect(screen.queryByTestId('passport-art')).toBeNull()
    expect(screen.getByRole('button', { name: /Try again/i })).toBeTruthy()
  })

  it('preserves the local-preview exhibition context in the back link', () => {
    window.history.replaceState({}, '', '/exhibition/passport/view?exhibition=local-preview')
    render(<ExhibitionPassportViewScreen />)

    expect(screen.getByRole('link', { name: /Back to My Pet ID/i }).getAttribute('href')).toBe(
      '/exhibition/passport?exhibition=local-preview',
    )
    expect(retrievePetPassport).not.toHaveBeenCalled()
  })
})
