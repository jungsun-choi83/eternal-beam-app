import { Check, Copy, Globe2, PawPrint, Plane } from 'lucide-react'
import { useState } from 'react'

interface PassportIdCardLabels {
  official: string
  edition: string
  petId: string
  copy: string
  copied: string
  copyFailed: string
}

interface PassportIdCardProps {
  petId: string
  labels: PassportIdCardLabels
}

/** A reusable, data-driven ivory Passport insert for the ID-created step. */
export function PassportIdCard({ petId, labels }: PassportIdCardProps) {
  const [copied, setCopied] = useState(false)
  const [copyFailed, setCopyFailed] = useState(false)

  async function copyId() {
    try {
      await navigator.clipboard.writeText(petId)
      setCopyFailed(false)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
      setCopyFailed(true)
    }
  }

  return (
    <div className="passport-id-card-wrap">
      <article className="passport-id-created-card" aria-label={`${labels.petId} ${petId}`}>
        <div className="passport-id-card-inner-border">
          <header className="passport-id-card-heading">
            <span>{labels.official}</span>
            <small>{labels.edition}</small>
          </header>

          <div className="passport-id-card-emblem" aria-hidden="true">
            <Globe2 size={58} strokeWidth={1.05} />
            <PawPrint className="passport-id-card-paw" size={22} fill="currentColor" strokeWidth={1.1} />
          </div>

          <div className="passport-id-card-value">
            <span>{labels.petId}</span>
            <strong>{petId}</strong>
          </div>

          <div className="passport-id-card-stamp" aria-hidden="true">
            <Plane size={18} strokeWidth={1.2} />
            <span>EXHIBITION</span>
          </div>
        </div>
      </article>

      <div className="passport-id-card-copy-area">
        <button className="passport-id-copy-button" type="button" onClick={copyId} aria-live="polite">
          {copied ? <Check size={19} /> : <Copy size={19} />}
          {copied ? labels.copied : labels.copy}
        </button>
        {copyFailed && <small className="passport-id-copy-error" role="status">{labels.copyFailed}</small>}
      </div>
    </div>
  )
}
