// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// If any Firebase module is evaluated while the public route renders, this
// test fails exactly like a missing production Firebase configuration would.
vi.mock('firebase/app', () => {
  throw new Error('Firebase must not initialize for the public Passport entry')
})
vi.mock('firebase/auth', () => {
  throw new Error('Firebase auth must not load for the public Passport entry')
})
vi.mock('firebase/firestore', () => {
  throw new Error('Firebase Firestore must not load for the public Passport entry')
})

import App from './App'

afterEach(() => {
  cleanup()
  window.history.replaceState({}, '', '/')
  window.localStorage.clear()
})

describe('public Passport entry isolation', () => {
  it('renders without evaluating Firebase when Firebase configuration is invalid', () => {
    window.history.replaceState({}, '', '/exhibition/passport')
    render(<App />)
    expect(screen.getByRole('button', { name: /Start Registration/i })).toBeTruthy()
    expect(screen.getByText('Get Your Pet ID')).toBeTruthy()
  })
})
