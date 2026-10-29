import { type FormEvent, useEffect, useState } from 'react'
import { MailCheck } from 'lucide-react'
import { savePetPassportEmail } from '@/lib/exhibition-passport-api'

export interface PassportEmailLabels {
  title: string
  body: string
  placeholder: string
  notify: string
  saving: string
  invalid: string
  unavailable: string
  saved: string
  aiVideoConsent: string
  marketingConsent: string
}

function validEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim())
}

/** Optional Eternal Beam notification UI shared by the active Passport flow. */
export function PassportEmailSection({
  labels,
  passportToken,
  emailSaved = false,
  aiVideoConsent: initialAiVideoConsent = false,
  marketingConsent: initialMarketingConsent = false,
  onEmailEntered,
}: {
  labels: PassportEmailLabels
  passportToken: string
  emailSaved?: boolean
  aiVideoConsent?: boolean
  marketingConsent?: boolean
  onEmailEntered?: () => void
}) {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState<string | null>(emailSaved ? labels.saved : null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [aiConsent, setAiConsent] = useState(initialAiVideoConsent)
  const [marketing, setMarketing] = useState(initialMarketingConsent)

  useEffect(() => {
    if (emailSaved) setMessage(labels.saved)
    setAiConsent(initialAiVideoConsent)
    setMarketing(initialMarketingConsent)
  }, [emailSaved, initialAiVideoConsent, initialMarketingConsent, labels.saved])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    setMessage(null)
    if (!validEmail(email)) {
      setError(labels.invalid)
      return
    }
    setSaving(true)
    try {
      await savePetPassportEmail({
        passportToken,
        email,
        aiVideoConsent: aiConsent,
        marketingConsent: marketing,
      })
      setMessage(labels.saved)
    } catch {
      setError(labels.unavailable)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="passport-final-email" aria-labelledby="passport-email-title">
      <div className="passport-final-section-icon"><MailCheck size={22} /></div>
      <div className="passport-final-section-copy">
        <h2 id="passport-email-title">{labels.title}</h2>
        <form onSubmit={submit}>
          <label className="passport-email-consent">
            <input type="checkbox" checked={aiConsent} onChange={(event) => setAiConsent(event.target.checked)} />
            <span>{labels.aiVideoConsent}</span>
          </label>
          <label className="passport-email-consent">
            <input type="checkbox" checked={marketing} onChange={(event) => setMarketing(event.target.checked)} />
            <span>{labels.marketingConsent}</span>
          </label>
          <div className="passport-final-email-row">
            <label className="sr-only" htmlFor="passport-email">{labels.placeholder}</label>
            <input
              id="passport-email"
              type="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value)
                if (event.target.value.trim()) onEmailEntered?.()
              }}
              placeholder={labels.placeholder}
              autoComplete="email"
            />
            <button type="submit" disabled={saving}>{saving ? labels.saving : labels.notify} <span aria-hidden="true">→</span></button>
          </div>
          {error && <p className="passport-final-error" role="alert">{error}</p>}
          {message && <p className="passport-final-email-message" role="status">{message}</p>}
        </form>
      </div>
    </section>
  )
}
