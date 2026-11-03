import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, Camera, Sparkles } from 'lucide-react'
import { PassportEmailSection, type PassportEmailLabels } from './passport-email-section'
import { SoulTracePromo } from './soultrace-promo'

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
  journeySteps: readonly PassportJourneyStep[]
  soulTraceTitle: string
  soulTraceBody: string
  soulTraceCta: string
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
  const [completedSteps, setCompletedSteps] = useState(() => [false, false, false, false, false])

  useEffect(() => {
    if (photoReceived) {
      setCompletedSteps((current) => current[0] ? current : current.map((complete, index) => index === 0 ? true : complete))
    }
  }, [photoReceived])

  useEffect(() => {
    const soulTraceChecked = completedSteps[2]

    if (soulTraceChecked) {
      setCompletedSteps((current) => current[3] ? current : current.map((complete, index) => index === 3 ? true : complete))
    }
  }, [completedSteps])

  function toggleStep(index: number) {
    // Only the Passport step remains manually actionable. The other steps are
    // display-only or driven by real/progression events.
    if (index !== 4) return
    if (index === 4) {
      setCompletedSteps((current) => current[4] ? current : current.map((complete, stepIndex) => stepIndex === 4 ? true : complete))
      return
    }
    setCompletedSteps((current) => current.map((complete, stepIndex) => stepIndex === index ? !complete : complete))
  }

  function renderEventStep({ title, description }: PassportJourneyStep, index: number, extraClass = '') {
    return (
      <div
        className={`passport-step passport-event-step ${extraClass} ${completedSteps[index] ? 'is-complete' : ''}`}
        id={`passport-event-step-${index + 1}`}
        key={title}
        role="listitem"
      >
        <button
          className="passport-event-step-icon"
          type="button"
          aria-pressed={completedSteps[index]}
          disabled={index !== 4}
          aria-label={index === 0
            ? 'Completed automatically when the exhibition photo is added'
            : index === 1 || index === 2
              ? 'Progress step'
            : index === 3
              ? 'Completed automatically through the exhibition journey'
            : (completedSteps[index] ? `Mark ${title} as incomplete` : `Mark ${title} as complete`)}
          onClick={() => toggleStep(index)}
        >
          <span className="passport-event-step-number" aria-hidden="true">
            {index + 1}
          </span>
        </button>
        <span className="passport-step-copy">
          <strong>{title}</strong>
          <small>{description}</small>
          {index === 1 && (
            <div className="passport-event-email-panel is-open" id="passport-email-panel">
              <PassportEmailSection
                labels={labels.email}
                passportToken={passportToken}
                emailSaved={emailSaved}
                aiVideoConsent={aiVideoConsent}
                marketingConsent={marketingConsent}
                onEmailEntered={() => {
                  setCompletedSteps((current) => current[1] ? current : current.map((complete, stepIndex) => stepIndex === 1 ? true : complete))
                }}
              />
            </div>
          )}
          {index === 2 && (
            <div className="passport-event-soultrace-panel is-open" id="passport-soul-trace-panel">
              <SoulTracePromo
                className="passport-event-soultrace-card"
                title={labels.soulTraceTitle}
                body={labels.soulTraceBody}
                cta={labels.soulTraceCta}
                minimal
                onCtaClick={() => {
                  setCompletedSteps((current) => current[2] ? current : current.map((complete, stepIndex) => stepIndex === 2 ? true : complete))
                  window.setTimeout(() => {
                    document.getElementById('passport-event-step-4')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
                  }, 0)
                }}
              />
            </div>
          )}
          {index === 4 && (
            <div className="passport-event-final-action">
              <div className="passport-photo-name-field">
                <label htmlFor="passport-pet-name">{labels.nameLabel}</label>
                <input
                  id="passport-pet-name"
                  type="text"
                  value={petName}
                    onChange={(event) => {
                      const value = event.target.value
                      onPetNameChange(value)
                      if (value.trim()) {
                        setCompletedSteps((current) => current[4] ? current : current.map((complete, index) => index === 4 ? true : complete))
                      }
                    }}
                  placeholder={labels.namePlaceholder}
                  maxLength={40}
                  autoComplete="off"
                />
                <small>{labels.nameHint}</small>
              </div>
              {error && <p className="passport-error passport-photo-error" role="alert">{error}</p>}
              <button
                className="passport-photo-continue"
                type="button"
                onClick={() => {
                  setCompletedSteps((current) => current.map((complete, index) => index === 4 ? true : complete))
                  onContinue()
                }}
                disabled={busy}
              >
                <Sparkles size={18} aria-hidden="true" /> {photoReceived ? labels.continueReady : labels.continueWaiting} <ArrowRight size={18} aria-hidden="true" />
              </button>
            </div>
          )}
        </span>
      </div>
    )
  }

  const photoBoothStep = labels.journeySteps[0]
  const remainingSteps = labels.journeySteps.slice(1)

  return (
    <section className="passport-photo-step" aria-labelledby="passport-photo-step-title">
      <div className="passport-photo-step-topbar">
        <button className="passport-photo-back-button" type="button" onClick={onBack}>
          <ArrowLeft size={17} aria-hidden="true" />
          <span>{labels.back}</span>
        </button>
      </div>

      <div className="passport-photo-page-id" aria-label={`Pet ID ${petId}`}>
        <strong>{petId}</strong>
      </div>

      <div className="passport-photo-heading passport-event-guide-heading">
        <h1 className="passport-screen-title" id="passport-photo-step-title">{labels.title}</h1>
      </div>

      {photoBoothStep && (
        <div className="passport-photo-booth-step" role="list" aria-label={photoBoothStep.title}>
          {renderEventStep(photoBoothStep, 0, 'is-photo-booth-step')}
        </div>
      )}

      <article className="passport-photo-document" aria-label={`${labels.petId} ${petId}`}>
        <div className="passport-photo-document-inner">
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

        </div>
      </article>

      <section className="passport-photo-journey" aria-labelledby="passport-photo-step-title">
        <div className="passport-steps" role="list">
          {remainingSteps.map((step, index) => renderEventStep(step, index + 1))}
        </div>
      </section>
    </section>
  )
}
