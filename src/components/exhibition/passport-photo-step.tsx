import { ArrowLeft, ArrowRight, BookOpenCheck, Camera, Check, Compass, Fingerprint, Globe2, MapPinned, RefreshCw, Sparkles } from 'lucide-react'
import { PassportEmailSection, type PassportEmailLabels } from './passport-email-section'

const journeyIcons = [Fingerprint, Camera, BookOpenCheck] as const

export interface PassportJourneyStep {
  title: string
  description: string
}

interface PassportPhotoStepLabels {
  back: string
  title: string
  description: string
  official: string
  edition: string
  photoWaiting: string
  photoAutomatic: string
  photoUnavailable: string
  petId: string
  petName: string
  noName: string
  photoProgressTitle: string
  photoProgressBody: string
  photoAddedTitle: string
  photoAddedBody: string
  refresh: string
  nameLabel: string
  namePlaceholder: string
  nameHint: string
  continueWaiting: string
  continueReady: string
  journeyTitle: string
  journeySteps: readonly PassportJourneyStep[]
  email: PassportEmailLabels
}

interface PassportPhotoStepProps {
  passportToken: string
  petId: string
  petName: string
  photoUrl: string | null
  photoReceived: boolean
  photoUnavailable: boolean
  busy: boolean
  error: string | null
  labels: PassportPhotoStepLabels
  emailSaved?: boolean
  aiVideoConsent?: boolean
  marketingConsent?: boolean
  onBack: () => void
  onRefresh: () => void
  onPetNameChange: (value: string) => void
  onContinue: () => void
}

export function PassportPhotoStep({
  passportToken,
  petId,
  petName,
  photoUrl,
  photoReceived,
  photoUnavailable,
  busy,
  error,
  labels,
  emailSaved,
  aiVideoConsent,
  marketingConsent,
  onBack,
  onRefresh,
  onPetNameChange,
  onContinue,
}: PassportPhotoStepProps) {
  return (
    <section className="passport-photo-step" aria-labelledby="passport-photo-step-title">
      <div className="passport-photo-step-topbar">
        <button className="passport-photo-back-button" type="button" onClick={onBack}>
          <ArrowLeft size={17} aria-hidden="true" />
          <span>{labels.back}</span>
        </button>
      </div>

      <div className="passport-photo-heading">
        <h1 className="passport-screen-title" id="passport-photo-step-title">{labels.title}</h1>
        <p>{labels.description}</p>
      </div>

      <article className="passport-photo-document" aria-label={`${labels.official} ${petId}`}>
        <div className="passport-photo-document-inner">
          <header className="passport-photo-document-header">
            <span>{labels.official}</span>
            <small>{labels.edition}</small>
          </header>

          <div className={`passport-photo-frame ${photoReceived ? 'has-photo' : ''}`}>
            {photoReceived && photoUrl ? (
              <img src={photoUrl} alt="" />
            ) : (
              <div className="passport-photo-empty" aria-live="polite">
                <Camera size={34} strokeWidth={1.2} aria-hidden="true" />
                <strong>{labels.photoWaiting}</strong>
                <small>{labels.photoAutomatic}</small>
              </div>
            )}
            <span className="passport-photo-frame-corner passport-photo-frame-corner-one" aria-hidden="true" />
            <span className="passport-photo-frame-corner passport-photo-frame-corner-two" aria-hidden="true" />
          </div>

          <div className="passport-photo-document-details">
            <div>
              <span>{labels.petId}</span>
              <strong>{petId}</strong>
            </div>
            <div>
              <span>{labels.petName}</span>
              <strong>{petName || labels.noName}</strong>
            </div>
          </div>

          <div className="passport-photo-document-compass" aria-hidden="true">
            <Compass size={58} strokeWidth={.85} />
            <Globe2 size={31} strokeWidth={.8} />
            <MapPinned size={14} strokeWidth={1} />
          </div>

          <div className="passport-photo-document-stamp" aria-hidden="true">
            <span>EXHIBITION</span>
            <span>EDITION</span>
          </div>

          <footer className="passport-photo-document-footer">OFFICIAL PET PASSPORT</footer>
        </div>
      </article>

      <section className={`passport-photo-status ${photoReceived ? 'is-added' : ''}`} aria-live="polite">
        <div className="passport-photo-status-icon">
          {photoReceived ? <Check size={20} aria-hidden="true" /> : <RefreshCw size={19} aria-hidden="true" />}
        </div>
        <div>
          <h2>{photoReceived ? labels.photoAddedTitle : labels.photoProgressTitle}</h2>
          <p>{photoReceived ? labels.photoAddedBody : photoUnavailable ? labels.photoUnavailable : labels.photoProgressBody}</p>
        </div>
        {!photoReceived && (
          <button className="passport-photo-refresh" type="button" onClick={onRefresh} disabled={busy}>
            <RefreshCw size={15} aria-hidden="true" /> {labels.refresh}
          </button>
        )}
      </section>

      <div className="passport-photo-name-field">
        <label htmlFor="passport-pet-name">{labels.nameLabel}</label>
        <input
          id="passport-pet-name"
          type="text"
          value={petName}
          onChange={(event) => onPetNameChange(event.target.value)}
          placeholder={labels.namePlaceholder}
          maxLength={40}
          autoComplete="off"
        />
        <small>{labels.nameHint}</small>
      </div>

      {error && <p className="passport-error passport-photo-error" role="alert">{error}</p>}

      <button className="passport-photo-continue" type="button" onClick={onContinue} disabled={busy}>
        <Sparkles size={18} aria-hidden="true" /> {photoReceived ? labels.continueReady : labels.continueWaiting} <ArrowRight size={18} aria-hidden="true" />
      </button>

      <section className="passport-photo-journey" aria-labelledby="passport-photo-journey-title">
        <div className="passport-section-heading">
          <span className="passport-overline" id="passport-photo-journey-title">{labels.journeyTitle}</span>
        </div>
        <ol className="passport-steps">
          {labels.journeySteps.map(({ title, description }, index) => {
            const Icon = journeyIcons[index]
            const complete = index === 0
            const current = index === 1
            return (
              <li className={`passport-step ${complete ? 'is-complete' : ''} ${current ? 'is-current' : ''}`} key={title}>
                <span className="passport-step-visual">
                  <span className="passport-step-icon"><Icon size={18} strokeWidth={1.5} /></span>
                </span>
                <span className="passport-step-copy"><strong>{title}</strong><small>{description}</small></span>
              </li>
            )
          })}
        </ol>
      </section>

      <PassportEmailSection
        labels={labels.email}
        passportToken={passportToken}
        emailSaved={emailSaved}
        aiVideoConsent={aiVideoConsent}
        marketingConsent={marketingConsent}
      />
    </section>
  )
}
