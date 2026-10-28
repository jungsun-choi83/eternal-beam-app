// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const sharePetPassport = vi.fn()
const downloadPetPassport = vi.fn()

vi.mock('@/lib/exhibition-passport-download', () => ({
  sharePetPassport: (...args: unknown[]) => sharePetPassport(...args),
  downloadPetPassport: (...args: unknown[]) => downloadPetPassport(...args),
}))

import { PassportFinalStep } from './passport-final-step'

const labels = {
  back: 'Back', titleReady: 'YOUR PASSPORT IS READY', titleWaiting: 'YOUR PASSPORT IS ALMOST READY',
  subtitleReady: 'Ready', subtitleWaiting: 'Waiting', download: 'Download', downloading: 'Downloading',
  share: 'Share Passport', sharing: 'Sharing', shareHint: 'Choose an app.', shared: 'Shared',
  shareFallback: 'Downloaded', shareFailed: 'Failed', photoPending: 'Pending', photoPendingBody: 'Pending',
  photoUnavailable: 'Unavailable', soulTraceTitle: 'Soul Trace', soulTraceBody: 'Story', soulTraceCta: 'Explore',
  shareInstagram: 'Share to Instagram', shareFacebook: 'Share to Facebook', shareKakao: 'Share to KakaoTalk',
  shareInstagramFallback: 'Downloaded for Instagram', shareFacebookOpened: 'Facebook opened',
  shareKakaoUnavailable: 'Kakao unavailable',
}

const record = {
  exhibition_id: 'seoul-2026', queue_number: 42, pet_name: 'Momo',
  photo_status: 'PHOTO_RECEIVED' as const, photo_received_at: '2026-10-20T00:00:00Z',
  created_at: '2026-10-20T00:00:00Z', photo_url: 'https://storage.example/pet.png',
}

afterEach(() => {
  cleanup()
  sharePetPassport.mockReset()
  downloadPetPassport.mockReset()
})

describe('PassportFinalStep sharing controls', () => {
  it('keeps platform icons hidden until Share Passport is expanded, then collapses them', () => {
    render(<PassportFinalStep record={record} petId="#042" petName="Momo" language="en" photoReady labels={labels} onBack={() => {}} />)
    const shareButton = screen.getByRole('button', { name: 'Share Passport' })
    const options = document.getElementById('passport-share-options') as HTMLElement

    expect(options.className).not.toContain('is-open')
    fireEvent.click(shareButton)
    expect(options.className).toContain('is-open')
    expect(screen.getByRole('button', { name: 'Share to Instagram' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Share to Facebook' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Share to KakaoTalk' })).toBeTruthy()

    fireEvent.click(shareButton)
    expect(options.className).not.toContain('is-open')
  })

  it('uses the generated Passport image path for Instagram sharing', async () => {
    sharePetPassport.mockResolvedValue('shared')
    render(<PassportFinalStep record={record} petId="#042" petName="Momo" language="en" photoReady labels={labels} onBack={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Share Passport' }))
    fireEvent.click(screen.getByRole('button', { name: 'Share to Instagram' }))

    await vi.waitFor(() => expect(sharePetPassport).toHaveBeenCalledWith(record, 'en', '#042', 'Seoul, Korea'))
  })
})
