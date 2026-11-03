import { useEffect, useState } from 'react'
import { ArrowLeft, Download, Languages, RefreshCw, Sparkles } from 'lucide-react'
import {
  exhibitionPassportExhibitionId,
  retrievePetPassport,
  type PassportRecord,
} from '@/lib/exhibition-passport-api'
import { downloadPetPassport } from '@/lib/exhibition-passport-download'
import { passportStage } from '@/lib/exhibition-passport-flow'
import {
  readPassportSession,
  savePassportSession,
  type PassportSession,
} from '@/lib/exhibition-passport-session'
import { PassportArtwork, type PassportLanguage } from './exhibition-passport-artwork'

const languageStorageKey = 'eternal_beam_exhibition_passport_language_v1'

const copy = {
  en: {
    brand: 'Eternal Beam',
    soulTrace: 'Soul Trace exhibition',
    title: 'Your Pet Passport',
    intro: 'Your exhibition portrait, preserved as a digital keepsake.',
    back: 'Back to My Pet ID',
    waitingTitle: 'Your Passport is being prepared',
    waitingBody: 'Your exhibition photo has not been added yet. Show your Pet ID to the exhibition team, then check again here.',
    refresh: 'Refresh Status',
    refreshing: 'Checking…',
    download: 'Download Passport',
    downloading: 'Preparing download…',
    unavailableTitle: 'Your Passport photo is temporarily unavailable',
    unavailableBody: 'We found your Passport, but the photo could not be loaded. Please try again.',
    noSessionTitle: 'Open your Pet ID first',
    noSessionBody: 'Your private Passport session is not available on this device. Return to My Pet ID to continue.',
    error: 'We could not load your Passport. Please try again or return to My Pet ID.',
    retry: 'Try again',
    language: '한국어',
  },
  ko: {
    brand: 'Eternal Beam',
    soulTrace: 'Soul Trace 전시',
    title: '나의 Pet Passport',
    intro: '전시에서 완성된 반려동물의 디지털 기록입니다.',
    back: '내 Pet ID로 돌아가기',
    waitingTitle: 'Passport를 준비하고 있어요',
    waitingBody: '아직 전시 사진이 연결되지 않았습니다. Pet ID를 전시 스태프에게 보여준 뒤 다시 확인해 주세요.',
    refresh: '상태 새로고침',
    refreshing: '확인 중…',
    download: 'Passport 다운로드',
    downloading: '다운로드 준비 중…',
    unavailableTitle: 'Passport 사진을 잠시 불러올 수 없습니다',
    unavailableBody: 'Passport는 찾았지만 사진을 불러오지 못했습니다. 다시 시도해 주세요.',
    noSessionTitle: '먼저 Pet ID를 열어 주세요',
    noSessionBody: '이 기기에서 비공개 Passport 세션을 찾을 수 없습니다. 내 Pet ID로 돌아가 계속해 주세요.',
    error: 'Passport를 불러오지 못했습니다. 다시 시도하거나 내 Pet ID로 돌아가 주세요.',
    retry: '다시 시도',
    language: 'English',
  },
} as const

function initialLanguage(): PassportLanguage {
  if (typeof window !== 'undefined') {
    try {
      const stored = window.localStorage.getItem(languageStorageKey)
      if (stored === 'en' || stored === 'ko') return stored
    } catch {
      // Language persistence is optional.
    }
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
    email_saved: session.emailSaved,
    ai_video_consent: session.aiVideoConsent,
    marketing_consent: session.marketingConsent,
  }
}

function dashboardHref(): string {
  return `/exhibition/passport${typeof window !== 'undefined' ? window.location.search : ''}`
}

function errorStatus(error: unknown): number | null {
  if (!error || typeof error !== 'object' || !('status' in error)) return null
  const status = (error as { status?: unknown }).status
  return typeof status === 'number' ? status : null
}

export function ExhibitionPassportViewScreen() {
  const [language, setLanguage] = useState<PassportLanguage>(initialLanguage)
  const t = copy[language]
  const exhibitionId = exhibitionPassportExhibitionId()
  const [session] = useState<PassportSession | null>(() => readPassportSession(undefined, exhibitionId))
  const [record, setRecord] = useState<PassportRecord | null>(() => session ? recordFromSession(session) : null)
  const [busy, setBusy] = useState(Boolean(session?.passportToken))
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [photoUnavailable, setPhotoUnavailable] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    try {
      window.localStorage?.setItem(languageStorageKey, language)
    } catch {
      // Language persistence is optional.
    }
  }, [language])

  useEffect(() => {
    if (!session?.passportToken) return
    let active = true
    retrievePetPassport(session.passportToken)
      .then((fresh) => {
        if (!active) return
        setRecord(fresh)
        setPhotoUnavailable(false)
        setError(null)
        savePassportSession(fresh, session.passportToken, undefined, session.exhibitionId)
      })
      .catch((retrievalError: unknown) => {
        if (!active) return
        setError(errorStatus(retrievalError) === 404 ? t.noSessionBody : t.error)
      })
      .finally(() => {
        if (active) setBusy(false)
      })
    return () => {
      active = false
    }
    // The token is the visitor session identity; language changes must not refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.passportToken])

  async function refreshStatus() {
    if (!session?.passportToken || busy) return
    setBusy(true)
    setError(null)
    try {
      const fresh = await retrievePetPassport(session.passportToken)
      savePassportSession(fresh, session.passportToken, undefined, session.exhibitionId)
      setRecord(fresh)
      setPhotoUnavailable(false)
    } catch (refreshError: unknown) {
      setError(errorStatus(refreshError) === 404 ? t.noSessionBody : t.error)
    } finally {
      setBusy(false)
    }
  }

  async function downloadPassport() {
    if (!record?.photo_url || photoUnavailable || downloadBusy) return
    setDownloadBusy(true)
    setError(null)
    try {
      await downloadPetPassport(record, fetch, undefined, language)
    } catch {
      setError(t.error)
    } finally {
      setDownloadBusy(false)
    }
  }

  function toggleLanguage() {
    setLanguage((previous) => previous === 'en' ? 'ko' : 'en')
  }

  const stage = passportStage(record)
  const hasReadyPhoto = stage === 'ready' && Boolean(record?.photo_url) && !photoUnavailable
  const backHref = dashboardHref()

  return (
    <main className="passport-page passport-view-page">
      <header className="passport-header passport-view-header">
        <a className="passport-brand" href={backHref} aria-label="Eternal Beam Pet Passport">
          <span className="passport-brand-mark"><Sparkles size={16} strokeWidth={1.5} /></span>
          <span>{t.brand}</span>
          <span className="passport-brand-divider">×</span>
          <span className="passport-brand-soul">{t.soulTrace}</span>
        </a>
        <button className="passport-language" type="button" onClick={toggleLanguage} aria-label={`Switch language to ${t.language}`}>
          <Languages size={16} /> {t.language}
        </button>
      </header>

      <section className="passport-view-shell" aria-labelledby="passport-view-title">
        <div className="passport-view-heading">
          <span className="passport-kicker"><span /> Pet Passport</span>
          <h1 className="passport-screen-title" id="passport-view-title">{t.title}</h1>
          <p>{t.intro}</p>
        </div>

        {!session && (
          <section className="passport-view-state" aria-live="polite">
            <span className="passport-status-icon waiting"><ArrowLeft size={23} /></span>
            <h2>{t.noSessionTitle}</h2>
            <p>{t.noSessionBody}</p>
            <a className="passport-primary-button passport-link-button" href={backHref}>{t.back}</a>
          </section>
        )}

        {session && busy && !hasReadyPhoto && (
          <section className="passport-view-state" aria-live="polite">
            <span className="passport-status-icon waiting"><RefreshCw className="passport-spin" size={23} /></span>
            <h2>{t.refreshing}</h2>
          </section>
        )}

        {session && !busy && !error && stage === 'waiting' && (
          <section className="passport-view-state" aria-live="polite">
            <span className="passport-status-icon waiting"><RefreshCw size={23} /></span>
            <h2>{t.waitingTitle}</h2>
            <p>{t.waitingBody}</p>
            <div className="passport-view-actions">
              <a className="passport-secondary-button passport-link-button" href={backHref}>{t.back}</a>
            </div>
          </section>
        )}

        {session && !busy && !error && stage === 'ready' && !hasReadyPhoto && (
          <section className="passport-view-state" aria-live="polite">
            <span className="passport-status-icon waiting"><RefreshCw size={23} /></span>
            <h2>{t.unavailableTitle}</h2>
            <p>{t.unavailableBody}</p>
            <div className="passport-view-actions">
              <button className="passport-primary-button" type="button" onClick={refreshStatus} disabled={busy}>
                <RefreshCw size={17} /> {t.retry}
              </button>
              <a className="passport-secondary-button passport-link-button" href={backHref}>{t.back}</a>
            </div>
          </section>
        )}

        {session && !busy && !error && hasReadyPhoto && record && (
          <section className="passport-view-passport" aria-label={t.title}>
            <PassportArtwork
              record={record}
              language={language}
              photoUnavailable={photoUnavailable}
              onPhotoError={() => setPhotoUnavailable(true)}
            />
            <div className="passport-view-actions">
              <button className="passport-primary-button" type="button" onClick={downloadPassport} disabled={downloadBusy}>
                <Download size={17} /> {downloadBusy ? t.downloading : t.download}
              </button>
              <a className="passport-secondary-button passport-link-button" href={backHref}>
                <ArrowLeft size={16} /> {t.back}
              </a>
            </div>
            <div className="passport-soultrace-promo">
              <a
                className="passport-soultrace-banner"
                data-testid="passport-soultrace-banner"
                href="https://soultrace.pet"
                target="_blank"
                rel="noopener noreferrer"
                aria-label={language === 'ko' ? '소울트레이스 알아보기' : 'Discover Soul Trace'}
              >
                <img
                  src={`/home/soultrace(${language === 'ko' ? 'ko' : 'eng'})-cropped.png`}
                  alt={language === 'ko' ? '소울트레이스 소개 배너' : 'Discover Soul Trace promotional banner'}
                />
              </a>
            </div>
          </section>
        )}

        {error && (
          <section className="passport-view-error" role="alert">
            <p>{error}</p>
            <button className="passport-secondary-button" type="button" onClick={refreshStatus} disabled={busy}>{t.retry}</button>
          </section>
        )}
      </section>
    </main>
  )
}
