import { useState } from 'react'
import { ArrowLeft, ArrowRight, BookOpen, Camera, Download, Gift, PlayCircle, Sparkles } from 'lucide-react'
import { PassportEmailSection, type PassportEmailLabels } from './passport-email-section'

const journeyIcons = [Camera, PlayCircle, BookOpen, Gift, Download] as const

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
  const [emailExpanded, setEmailExpanded] = useState(false)

  function toggleStep(index: number) {
    setCompletedSteps((current) => current.map((complete, stepIndex) => stepIndex === index ? !complete : complete))
    if (index === 1) setEmailExpanded((expanded) => !expanded)
  }

  function renderEventStep({ title, description }: PassportJourneyStep, index: number, extraClass = '') {
    const Icon = journeyIcons[index]
    return (
      <div className={`passport-step passport-event-step ${extraClass} ${completedSteps[index] ? 'is-complete' : ''}`} key={title} role="listitem">
        <button
          className="passport-event-step-icon"
          type="button"
          aria-pressed={completedSteps[index]}
          aria-expanded={index === 1 ? emailExpanded : undefined}
          aria-controls={index === 1 ? 'passport-email-panel' : undefined}
          aria-label={index === 1
            ? (emailExpanded ? 'Hide Eternal Beam email options' : 'Show Eternal Beam email options')
            : (completedSteps[index] ? `Mark ${title} as incomplete` : `Mark ${title} as complete`)}
          onClick={() => toggleStep(index)}
        >
          <Icon size={19} strokeWidth={1.5} aria-hidden="true" />
        </button>
        <span className="passport-step-copy">
          <strong>{title}</strong>
          <small>{description}</small>
          {index === 1 && (
            <div
              className={`passport-event-email-panel ${emailExpanded ? 'is-open' : ''}`}
              id="passport-email-panel"
              aria-hidden={!emailExpanded}
            >
              <PassportEmailSection
                labels={labels.email}
                passportToken={passportToken}
                emailSaved={emailSaved}
                aiVideoConsent={aiVideoConsent}
                marketingConsent={marketingConsent}
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
