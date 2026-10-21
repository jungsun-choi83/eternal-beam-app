import { useEffect, useMemo, useState } from 'react'
import {
  ArrowRight,
  Camera,
  Check,
  Clock3,
  Copy,
  KeyRound,
  Languages,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { formatQueueNumber } from '@/lib/exhibition-queue'
import {
  createPassportToken,
  exhibitionPassportExhibitionId,
  isExhibitionPassportEnabled,
  isExhibitionPassportLocalPreview,
  registerPetPassport,
  retrievePetPassport,
  type PassportRecord,
  type RegistrationResponse,
} from '@/lib/exhibition-passport-api'
import { passportStage, type PassportStage } from '@/lib/exhibition-passport-flow'
import {
  readPassportSession,
  savePassportSession,
  clearPassportSession,
  readPendingPassportRegistration,
  savePendingPassportRegistration,
  clearPendingPassportRegistration,
  type PassportSession,
} from '@/lib/exhibition-passport-session'

type Language = 'en' | 'ko'
type PassportView = 'welcome' | 'register'

const languageStorageKey = 'eternal_beam_exhibition_passport_language_v1'

const copy = {
  en: {
    brand: 'Eternal Beam',
    soulTrace: 'Soul Trace exhibition',
    eyebrow: 'Pet Passport',
    title: 'A living keepsake for your companion.',
    intro: 'Register your pet before the exhibition. Your Pet ID connects your visit to a portrait captured by our exhibition team.',
    welcomeLabel: 'Visitor journey',
    welcomeTitle: 'Bring your companion into the light.',
    welcomeBody: 'Four simple steps connect your visit to a digital Pet Passport made at the exhibition.',
    startRegistration: 'Start Registration',
    register: 'Register your pet',
    journey: 'Your journey',
    steps: [
      ['Register your pet', 'Tell us your pet’s name and where to send updates.'],
      ['Get Your Pet ID', 'A unique queue number is issued by the exhibition.'],
      ['Visit the Photo Booth', 'Show your ID to staff, who take the photo.'],
      ['Receive Your Passport', 'View and download it when ready.'],
    ],
    petName: 'Pet name',
    petNameHint: 'The name you would like shown on the Passport',
    email: 'Email address',
    emailHint: 'Used only for your Passport experience',
    privacy: 'I agree to the Privacy Policy and consent to the use of this information for the Pet Passport experience.',
    privacyRequired: 'Privacy consent is required to register.',
    continue: 'Create my Pet ID',
    creating: 'Creating your Pet ID…',
    secure: 'Your details stay private. We never display your email publicly.',
    disabled: 'Passport registration is in preview mode. Enable the local feature flag to submit a registration.',
    idTitle: 'Your Pet ID / Queue Number',
    previewId: 'LOCAL PREVIEW ID',
    copyId: 'Copy ID',
    copied: 'Copied',
    idNote: 'This is your unique number. Keep this page on your phone and show it to exhibition staff for photo capture.',
    photoPlaceholder: 'Your exhibition portrait will appear here once the staff photo is connected.',
    waitingTitle: 'Waiting for staff photo',
    waitingBody: 'Your registration is saved. When you arrive, show your Pet ID to the exhibition team so they can capture your pet’s portrait.',
    viewPassport: 'View My Passport',
    refresh: 'Check again',
    readyTitle: 'Your Passport is ready',
    readyBody: 'Your original exhibition portrait is ready to view and download.',
    photoUnavailable: 'Your photo is linked, but is temporarily unavailable. Refresh to try again.',
    download: 'Download Passport',
    downloading: 'Preparing download…',
    downloadFailed: 'The Passport image could not be downloaded. Please refresh and try again.',
    passportPhotoAlt: 'Exhibition portrait for your Pet Passport',
    back: 'Register another pet',
    localOnly: 'Keep this browser or device available. Your private retrieval token is stored locally and is never placed in the URL.',
    unavailable: 'We could not reach the Passport service. Please try again in a moment.',
    notFound: 'We could not find this Passport on the server. Please register again.',
    retry: 'Try again',
    consentError: 'Please accept the Privacy Policy to continue.',
    language: '한국어',
  },
  ko: {
    brand: 'Eternal Beam',
    soulTrace: 'Soul Trace 전시',
    eyebrow: 'Pet Passport',
    title: '반려동물과 함께 남기는 살아 있는 기록.',
    intro: '전시 방문 전에 반려동물을 등록하세요. 전시팀이 촬영한 초상과 방문을 Pet ID로 연결해 드립니다.',
    register: '반려동물 등록',
    journey: '방문 여정',
    steps: [
      ['반려동물 등록', 'Pet Passport에 표시할 이름과 이메일을 입력합니다.'],
      ['Pet ID 받기', '전시에서 고유 대기번호를 발급합니다.'],
      ['전시팀 만나기', '사진 부스에서 같은 번호를 보여주세요.'],
      ['Passport 간직하기', '사진이 준비되면 확인하고 다운로드합니다.'],
    ],
    petName: '반려동물 이름',
    petNameHint: 'Passport에 표시할 이름',
    email: '이메일 주소',
    emailHint: 'Pet Passport 안내에만 사용합니다',
    privacy: '개인정보처리방침에 동의하며 Pet Passport 경험을 위해 정보가 사용되는 것에 동의합니다.',
    privacyRequired: '등록하려면 개인정보 동의가 필요합니다.',
    continue: 'Pet ID 만들기',
    creating: 'Pet ID를 만드는 중…',
    secure: '입력한 정보는 안전하게 보호됩니다. 이메일은 공개되지 않습니다.',
    disabled: 'Passport 등록은 미리보기 모드입니다. 로컬 기능 플래그를 켜면 등록할 수 있습니다.',
    idTitle: 'Pet ID / 대기번호',
    previewId: '로컬 프리뷰 ID',
    idNote: '고유 번호입니다. 이 화면을 휴대폰에 보관하고 사진 촬영을 위해 전시팀에 보여주세요.',
    waitingTitle: '전시 사진을 기다리는 중',
    waitingBody: '등록이 저장되었습니다. 전시에 도착하면 Pet ID를 전시팀에 보여주고 반려동물 사진을 촬영해 주세요.',
    viewPassport: '내 Passport 보기',
    refresh: '다시 확인하기',
    readyTitle: 'Passport가 준비되었습니다',
    readyBody: '사진을 받았습니다. Passport 보기와 다운로드는 전시 사진 연동 단계에서 제공됩니다.',
    downloadSoon: '다운로드는 곧 제공됩니다',
    back: '다른 반려동물 등록',
    localOnly: '이 브라우저 또는 기기를 계속 사용해 주세요. 개인 토큰은 로컬에만 저장되며 URL에 표시되지 않습니다.',
    unavailable: 'Passport 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
    notFound: '서버에서 Passport를 찾을 수 없습니다. 다시 등록해 주세요.',
    retry: '다시 시도',
    consentError: '계속하려면 개인정보처리방침에 동의해 주세요.',
    welcomeLabel: '\uBC29\uBB38\uC790 \uC5EC\uC815',
    welcomeTitle: '\uBC18\uB824\uB3D9\uBB3C\uC744 \uBE5B\uC73C\uB85C \uC548\uB0B4\uD558\uC138\uC694.',
    welcomeBody: '\uB124 \uB2E8\uACC4\uB85C \uC804\uC2DC\uC5D0\uC11C \uB9CC\uB4E0 \uB514\uC9C0\uD138 Pet Passport\uC5D0 \uBC29\uBB38\uC744 \uC5F0\uACB0\uD569\uB2C8\uB2E4.',
    startRegistration: '\uB4F1\uB85D \uC2DC\uC791',
    copyId: '\uC544\uC774\uB514 \uBCF5\uC0AC',
    copied: '\uBCF5\uC0AC\uB428',
    photoPlaceholder: '\uC804\uC2DC\uD300\uC758 \uC0AC\uC9C4\uC774 \uC5F0\uACB0\uB418\uBA74 \uC774\uACF3\uC5D0 \uD45C\uC2DC\uB429\uB2C8\uB2E4.',
    photoUnavailable: '\uC0AC\uC9C4\uC740 \uC5F0\uACB0\uB418\uC5C8\uC9C0\uB9CC \uC7A0\uC2DC \uC0AC\uC6A9\uD560 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4. \uC0C8\uB85C\uACE0\uCE68\uD574 \uB2E4\uC2DC \uD655\uC778\uD574 \uC8FC\uC138\uC694.',
    download: '\uD328\uC2A4\uD3EC\uD2B8 \uB2E4\uC6B4\uB85C\uB4DC',
    downloading: '\uB2E4\uC6B4\uB85C\uB4DC \uC900\uBE44 \uC911…',
    downloadFailed: '\uD328\uC2A4\uD3EC\uD2B8 \uC774\uBBF8\uC9C0\uB97C \uB2E4\uC6B4\uB85C\uB4DC\uD560 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4. \uC0C8\uB85C\uACE0\uCE68 \uD6C4 \uB2E4\uC2DC \uC2DC\uB3C4\uD574 \uC8FC\uC138\uC694.',
    passportPhotoAlt: '\uD328\uC2A4\uD3EC\uD2B8 \uC804\uC2DC \uCD2C\uC601 \uC0AC\uC9C4',
    language: 'English',
  },
} as const

function initialLanguage(): Language {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage?.getItem(languageStorageKey)
    if (stored === 'en' || stored === 'ko') return stored
    if (navigator.language.toLowerCase().startsWith('ko')) return 'ko'
  }
  return 'en'
}

function recordFromSession(session: PassportSession): PassportRecord {
  return {
    exhibition_id: session.exhibitionId,
    queue_number: session.queueNumber,
    pet_name: session.petName,
    photo_status: session.photoStatus,
    photo_received_at: null,
    created_at: session.createdAt,
  }
}

function friendlyError(error: unknown, t: (typeof copy)[Language]): string {
  if (error && typeof error === 'object' && 'status' in error && (error as { status?: number }).status === 404) {
    return t.notFound
  }
  return t.unavailable
}

export function ExhibitionPassportScreen() {
  const [language, setLanguage] = useState<Language>(initialLanguage)
  const t = copy[language]
  const exhibitionId = exhibitionPassportExhibitionId()
  const featureEnabled = isExhibitionPassportEnabled()
  const localPreview = isExhibitionPassportLocalPreview()
  const [session, setSession] = useState<PassportSession | null>(() => readPassportSession(undefined, exhibitionId))
  const [pendingRegistration] = useState(() => readPendingPassportRegistration(undefined, exhibitionId))
  const [record, setRecord] = useState<PassportRecord | null>(() => {
    const stored = readPassportSession(undefined, exhibitionId)
    return stored ? recordFromSession(stored) : null
  })
  const [stage, setStage] = useState<PassportStage>(() => passportStage(record))
  const [petName, setPetName] = useState(pendingRegistration?.petName || '')
  const [email, setEmail] = useState(pendingRegistration?.email || '')
  const [consent, setConsent] = useState(pendingRegistration?.privacyConsent === true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<PassportView>(() => (session || pendingRegistration ? 'register' : 'welcome'))
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    try {
      window.localStorage?.setItem(languageStorageKey, language)
    } catch {
      // Language persistence is a convenience, not a dependency.
    }
  }, [language])

  useEffect(() => {
    if (!session?.passportToken) return
    let active = true
    setBusy(true)
    retrievePetPassport(session.passportToken)
      .then((fresh) => {
        if (!active) return
        setRecord(fresh)
        setStage(passportStage(fresh))
        savePassportSession(fresh, session.passportToken, undefined, session.exhibitionId)
      })
      .catch((retrievalError: unknown) => {
        if (!active) return
        setError(friendlyError(retrievalError, t))
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    return () => {
      active = false
    }
    // The token is the session identity. Language changes should not trigger a network call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.passportToken])

  const progressStep = useMemo(() => {
    if (stage === 'register') return 1
    if (stage === 'waiting') return 3
    return 4
  }, [stage])

  function toggleLanguage() {
    setLanguage((previous) => (previous === 'en' ? 'ko' : 'en'))
  }

  function startRegistration() {
    setError(null)
    setView('register')
  }

  async function copyPetId() {
    if (!record) return
    const petId = formatQueueNumber(record.queue_number)
    try {
      await navigator.clipboard.writeText(petId)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }

  async function submitRegistration(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!consent) {
      setError(t.consentError)
      return
    }
    if (!featureEnabled) return
    setBusy(true)
    setError(null)
    try {
      const existingPending = pendingRegistration &&
        pendingRegistration.petName === petName && pendingRegistration.email === email
          ? pendingRegistration
          : null
      const passportToken = existingPending?.passportToken || createPassportToken()
      savePendingPassportRegistration({
        passportToken,
        exhibitionId: exhibitionId || 'default',
        petName,
        email,
        privacyConsent: true,
        privacyPolicyVersion: 'v1',
      })
      const response: RegistrationResponse = await registerPetPassport({
        exhibitionId,
        petName,
        email,
        privacyConsent: consent,
        passportToken,
      })
      clearPendingPassportRegistration(undefined, response.exhibition_id)
      clearPendingPassportRegistration(undefined, exhibitionId)
      savePassportSession(response, response.passport_token, undefined, response.exhibition_id)
      const nextSession = {
        passportToken: response.passport_token,
        exhibitionId: response.exhibition_id,
        queueNumber: response.queue_number,
        petName: response.pet_name,
        photoStatus: response.photo_status,
        createdAt: response.created_at,
      } satisfies PassportSession
      setSession(nextSession)
      setRecord(response)
      setStage(passportStage(response))
    } catch (registrationError) {
      setError(friendlyError(registrationError, t))
    } finally {
      setBusy(false)
    }
  }

  async function refreshPassport() {
    if (!session?.passportToken || busy) return
    setBusy(true)
    setError(null)
    try {
      const fresh = await retrievePetPassport(session.passportToken)
      savePassportSession(fresh, session.passportToken, undefined, session.exhibitionId)
      setRecord(fresh)
      setStage(passportStage(fresh))
    } catch (retrievalError) {
      setError(friendlyError(retrievalError, t))
    } finally {
      setBusy(false)
    }
  }

  function resetRegistration() {
    clearPassportSession(undefined, session?.exhibitionId ?? exhibitionId)
    clearPendingPassportRegistration(undefined, exhibitionId)
    setSession(null)
    setRecord(null)
    setStage('register')
    setView('welcome')
    setPetName('')
    setEmail('')
    setConsent(false)
    setError(null)
  }

  const passportViewHref = `${'/exhibition/passport/view'}${typeof window !== 'undefined' ? window.location.search : ''}`

  return (
    <main className="passport-page">
      <div className="passport-orbit passport-orbit-one" aria-hidden="true" />
      <div className="passport-orbit passport-orbit-two" aria-hidden="true" />
      <header className="passport-header">
        <a className="passport-brand" href="/" aria-label="Eternal Beam home">
          <span className="passport-brand-mark"><Sparkles size={16} strokeWidth={1.5} /></span>
          <span>{t.brand}</span>
          <span className="passport-brand-divider">×</span>
          <span className="passport-brand-soul">{t.soulTrace}</span>
        </a>
        <button className="passport-language" type="button" onClick={toggleLanguage} aria-label={`Switch language to ${t.language}`}>
          <Languages size={16} /> {t.language}
        </button>
      </header>

      <section className="passport-shell">
        <div className="passport-hero">
          <div className="passport-kicker"><span /> {t.eyebrow}</div>
          <h1>{t.title}</h1>
          <p>{t.intro}</p>
        </div>

        <div className={`passport-experience-grid ${stage === 'ready' ? 'is-passport-ready' : ''}`}>
        <section className="passport-journey" aria-labelledby="passport-journey-title">
          <div className="passport-section-heading">
            <span className="passport-overline" id="passport-journey-title">{t.journey}</span>
            <span className="passport-step-count">{progressStep} / 4</span>
          </div>
          <ol className="passport-steps">
            {t.steps.map(([title, description], index) => {
              const step = index + 1
              const complete = step < progressStep
              const current = step === progressStep
              return (
                <li className={`passport-step ${complete ? 'is-complete' : ''} ${current ? 'is-current' : ''}`} key={title}>
                  <span className="passport-step-number">{complete ? <Check size={14} /> : step}</span>
                  <span className="passport-step-copy"><strong>{title}</strong><small>{description}</small></span>
                </li>
              )
            })}
          </ol>
        </section>

        {!session && view === 'welcome' && (
          <section className="passport-card passport-welcome-card" aria-labelledby="passport-welcome-title">
            <div className="passport-welcome-mark"><Sparkles size={25} strokeWidth={1.4} /></div>
            <span className="passport-overline">{t.welcomeLabel}</span>
            <h2 id="passport-welcome-title">{t.welcomeTitle}</h2>
            <p>{t.welcomeBody}</p>
            <button className="passport-primary-button" type="button" onClick={startRegistration}>
              {t.startRegistration} <ArrowRight size={17} />
            </button>
            {!featureEnabled && <p className="passport-preview-note" role="status">{t.disabled}</p>}
          </section>
        )}

        {!session && view === 'register' && stage === 'register' && (
          <section className="passport-card passport-registration" aria-labelledby="passport-registration-title">
            <div className="passport-card-heading">
              <div className="passport-icon"><KeyRound size={21} /></div>
              <div><span className="passport-overline">01 / 04</span><h2 id="passport-registration-title">{t.register}</h2></div>
            </div>
            <form onSubmit={submitRegistration} noValidate>
              <label className="passport-field">
                <span>{t.petName}</span>
                <input value={petName} onChange={(event) => setPetName(event.target.value)} placeholder={t.petNameHint} autoComplete="off" required maxLength={120} />
              </label>
              <label className="passport-field">
                <span>{t.email}</span>
                <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t.emailHint} autoComplete="email" required maxLength={320} />
              </label>
              <label className="passport-consent">
                <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} required />
                <span>{t.privacy}</span>
              </label>
              <div className="passport-security-note"><ShieldCheck size={16} /> {t.secure}</div>
              {!featureEnabled && <p className="passport-preview-note" role="status">{t.disabled}</p>}
              {error && <p className="passport-error" role="alert">{error}</p>}
              <button className="passport-primary-button" type="submit" disabled={busy || !featureEnabled}>
                {busy ? t.creating : t.continue} <ArrowRight size={17} />
              </button>
            </form>
          </section>
        )}

        {stage !== 'register' && record && (
          <section className="passport-card passport-status-card" aria-live="polite">
            <div className="passport-id-panel">
              <span className="passport-overline">{t.idTitle}</span>
              {localPreview && <span className="passport-preview-badge">{t.previewId}</span>}
              <strong>{formatQueueNumber(record.queue_number)}</strong>
              <div className="passport-id-meta">
                <span className="passport-id-pet">{record.pet_name}</span>
                <button className="passport-copy-button" type="button" onClick={copyPetId}>
                  {copied ? <Check size={15} /> : <Copy size={15} />} {copied ? t.copied : t.copyId}
                </button>
              </div>
            </div>
            {stage === 'waiting' && (
              <div className="passport-status-content">
                <div className="passport-photo-placeholder" aria-label={t.photoPlaceholder}>
                  <span className="passport-photo-placeholder-icon"><Camera size={28} /></span>
                  <span className="passport-photo-placeholder-label">{t.eyebrow}</span>
                  <small>{t.photoPlaceholder}</small>
                </div>
                <div className="passport-status-icon waiting"><Clock3 size={23} /></div>
                <span className="passport-overline">03 / 04</span>
                <h2>{t.waitingTitle}</h2>
                <p>{t.waitingBody}</p>
                <div className="passport-instruction"><Camera size={17} /><span>{t.idNote}</span></div>
                <div className="passport-button-row">
                  <a className="passport-primary-button passport-link-button" href={passportViewHref}>
                    <ArrowRight size={17} /> {t.viewPassport}
                  </a>
                  <button className="passport-secondary-button" type="button" onClick={refreshPassport} disabled={busy}>
                    <RefreshCw size={16} /> {t.refresh}
                  </button>
                </div>
              </div>
            )}
            {stage === 'ready' && (
              <div className="passport-ready-summary">
                <div className="passport-status-icon ready"><Check size={23} /></div>
                <span className="passport-overline">04 / 04</span>
                <h2>{t.readyTitle}</h2>
                <p>{record.photo_url ? t.readyBody : t.photoUnavailable}</p>
                <div className="passport-button-row">
                  <a className="passport-primary-button passport-link-button" href={passportViewHref}>
                    <ArrowRight size={17} /> {t.viewPassport}
                  </a>
                  <button className="passport-secondary-button" type="button" onClick={refreshPassport} disabled={busy}>
                    <RefreshCw size={16} /> {t.refresh}
                  </button>
                </div>
              </div>
            )}
            {error && <p className="passport-error" role="alert">{error}</p>}
            <p className="passport-local-note"><LockKeyhole size={14} /> {t.localOnly}</p>
            <button className="passport-text-button" type="button" onClick={resetRegistration}>{t.back}</button>
          </section>
        )}
        </div>
      </section>
      <footer className="passport-footer"><span>ETERNAL BEAM</span><span>SOUL TRACE / PET PASSPORT</span></footer>
    </main>
  )
}
