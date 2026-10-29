import { useState } from 'react'
import { Check, Download, ExternalLink, Share2 } from 'lucide-react'
import { downloadPetPassport, sharePetPassport } from '@/lib/exhibition-passport-download'
import {
  configuredExhibitionLocation,
  type PassportRecord,
} from '@/lib/exhibition-passport-api'
import {
  formatPassportIssuedDateSeoul,
  PASSPORT_ART_HEIGHT,
  PASSPORT_ART_WIDTH,
  PASSPORT_PHOTO_APERTURE_PATH,
  PASSPORT_PHOTO_IMAGE_BOX,
  PASSPORT_TEMPLATE_PATH,
  passportLocationLabel,
} from '@/lib/exhibition-passport-art'

export type PassportFinalLanguage = 'en' | 'ko'

type FinalLabels = {
  back: string
  titleReady: string
  titleWaiting: string
  subtitleReady: string
  subtitleWaiting: string
  download: string
  downloading: string
  share: string
  sharing: string
  shareHint: string
  shared: string
  shareFallback: string
  shareFailed: string
  shareInstagram: string
  shareFacebook: string
  shareKakao: string
  shareInstagramFallback: string
  shareFacebookOpened: string
  shareKakaoUnavailable: string
  photoPending: string
  photoPendingBody: string
  photoUnavailable: string
  soulTraceTitle: string
  soulTraceBody: string
  soulTraceCta: string
}

type PassportFinalStepProps = {
  record: PassportRecord
  petId: string
  petName: string
  language: PassportFinalLanguage
  photoReady: boolean
  labels: FinalLabels
  onBack: () => void
  onPhotoError?: () => void
}

type KakaoSdk = {
  isInitialized: () => boolean
  init: (appKey: string) => void
  Share: {
    sendDefault: (payload: Record<string, unknown>) => void
  }
}

function getKakaoAppKey(): string {
  try {
    return String(import.meta.env.VITE_KAKAO_APP_KEY || '').trim()
  } catch {
    return ''
  }
}

function loadKakaoSdk(): Promise<KakaoSdk> {
  return new Promise((resolve, reject) => {
    const existing = (window as Window & { Kakao?: KakaoSdk }).Kakao
    if (existing) {
      resolve(existing)
      return
    }

    const current = document.getElementById('kakao-js-sdk') as HTMLScriptElement | null
    if (current) {
      current.addEventListener('load', () => {
        const sdk = (window as Window & { Kakao?: KakaoSdk }).Kakao
        sdk ? resolve(sdk) : reject(new Error('Kakao SDK unavailable'))
      }, { once: true })
      current.addEventListener('error', () => reject(new Error('Kakao SDK failed to load')), { once: true })
      return
    }

    const script = document.createElement('script')
    script.id = 'kakao-js-sdk'
    script.async = true
    script.src = 'https://t1.kakaocdn.net/kakao_js_sdk/2.7.4/kakao.min.js'
    script.onload = () => {
      const sdk = (window as Window & { Kakao?: KakaoSdk }).Kakao
      sdk ? resolve(sdk) : reject(new Error('Kakao SDK unavailable'))
    }
    script.onerror = () => reject(new Error('Kakao SDK failed to load'))
    document.head.appendChild(script)
  })
}

function safePassportShareUrl(): string {
  if (typeof window === 'undefined') return 'https://eternalbeam.com/exhibition/passport'
  return new URL('/exhibition/passport', window.location.origin).toString()
}

function openExternal(url: string): boolean {
  if (typeof window === 'undefined') return false
  const opened = window.open(url, '_blank', 'noopener,noreferrer')
  if (opened) return true
  window.location.assign(url)
  return false
}

function SocialPlatformIcon({ platform }: { platform: 'instagram' | 'facebook' | 'kakao' }) {
  if (platform === 'instagram') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <rect x="4" y="4" width="16" height="16" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="12" cy="12" r="3.5" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="17.3" cy="6.8" r="1" fill="currentColor" />
      </svg>
    )
  }
  if (platform === 'facebook') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M14 8h3V4.5c-.6-.1-1.8-.2-3.2-.2-3.2 0-5.4 1.9-5.4 5.5V12H5v4h3.4v8h4.2v-8h3.5l.6-4h-4.1V10c0-1.2.3-2 1.4-2Z" fill="currentColor" />
      </svg>
    )
  }
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M4.2 5.4C5.8 3.7 8.4 3 12 3s6.2.7 7.8 2.4c1.3 1.4 1.7 3.3 1.1 5.4-.6 2.2-2.2 4-4.6 5.3l1.5 3.4-4.2-2.7c-.5.1-1 .1-1.6.1-3.6 0-6.2-.7-7.8-2.4-1.3-1.4-1.7-3.3-1.1-5.4.4-1.5.8-2.7 1.1-3.7Z" fill="currentColor" />
      <circle cx="9" cy="10" r=".8" fill="#2b2118" />
      <circle cx="12" cy="10" r=".8" fill="#2b2118" />
      <circle cx="15" cy="10" r=".8" fill="#2b2118" />
    </svg>
  )
}

export function PassportFinalStep({
  record,
  petId,
  petName,
  language,
  photoReady,
  labels,
  onBack,
  onPhotoError,
}: PassportFinalStepProps) {
  const [downloadBusy, setDownloadBusy] = useState(false)
  const [shareBusy, setShareBusy] = useState(false)
  const [shareExpanded, setShareExpanded] = useState(false)
  const [shareMessage, setShareMessage] = useState<string | null>(null)
  const [photoFailed, setPhotoFailed] = useState(false)
  const isReady = photoReady && Boolean(record.photo_url) && !photoFailed
  const issueDate = formatPassportIssuedDateSeoul(record.created_at)
  const location = passportLocationLabel(record.exhibition_id, configuredExhibitionLocation())

  async function download() {
    if (!isReady || downloadBusy) return
    setDownloadBusy(true)
    try {
      await downloadPetPassport(record, fetch, undefined, language, petId, location)
    } finally {
      setDownloadBusy(false)
    }
  }

  function toggleShareMenu() {
    if (!isReady || shareBusy) return
    setShareMessage(null)
    setShareExpanded((expanded) => !expanded)
  }

  async function shareInstagram() {
    if (!isReady || shareBusy) return
    setShareBusy(true)
    setShareMessage(null)
    try {
      const result = await sharePetPassport(record, language, petId, location)
      if (result === 'shared') {
        setShareMessage(labels.shared)
      } else {
        openExternal('https://www.instagram.com/')
        setShareMessage(labels.shareInstagramFallback)
      }
    } catch {
      setShareMessage(labels.shareFailed)
    } finally {
      setShareBusy(false)
    }
  }

  function shareFacebook() {
    if (!isReady || shareBusy) return
    setShareMessage(null)
    const shareUrl = `https://www.facebook.com/sharer/sharer.php?u=${encodeURIComponent(safePassportShareUrl())}`
    openExternal(shareUrl)
    setShareMessage(labels.shareFacebookOpened)
  }

  async function shareKakao() {
    if (!isReady || shareBusy) return
    setShareBusy(true)
    setShareMessage(null)
    try {
      const appKey = getKakaoAppKey()
      if (appKey) {
        const kakao = await loadKakaoSdk()
        if (!kakao.isInitialized()) kakao.init(appKey)
        const shareUrl = safePassportShareUrl()
        kakao.Share.sendDefault({
          objectType: 'feed',
          content: {
            title: 'Pet Passport',
            description: 'A keepsake from the exhibition.',
            imageUrl: new URL(PASSPORT_TEMPLATE_PATH, window.location.origin).toString(),
            link: { mobileWebUrl: shareUrl, webUrl: shareUrl },
          },
          buttons: [{ title: 'View Passport', link: { mobileWebUrl: shareUrl, webUrl: shareUrl } }],
        })
        setShareMessage(labels.shared)
      } else {
        const result = await sharePetPassport(record, language, petId, location)
        setShareMessage(result === 'shared' ? labels.shared : labels.shareKakaoUnavailable)
      }
    } catch {
      setShareMessage(labels.shareKakaoUnavailable)
    } finally {
      setShareBusy(false)
    }
  }

  return (
    <section className="passport-final-step" aria-label="Pet Passport">
      <div className="passport-final-topbar">
        <button className="passport-final-back" type="button" onClick={onBack}>
          ← {labels.back}
        </button>
      </div>

      <div className="passport-final-booklet-wrap">
        <div
          className="passport-final-booklet"
          data-testid="passport-final-booklet"
        >
          <img className="passport-final-template" src={PASSPORT_TEMPLATE_PATH} alt="" aria-hidden="true" />
          {isReady && record.photo_url && (
            <svg
              className="passport-final-photo"
              viewBox={`0 0 ${PASSPORT_ART_WIDTH} ${PASSPORT_ART_HEIGHT}`}
              role="img"
              aria-label="Your pet's exhibition photograph"
            >
              <defs>
                <clipPath id="passport-final-photo-clip">
                  <path d={PASSPORT_PHOTO_APERTURE_PATH} />
                </clipPath>
              </defs>
              <image
                href={record.photo_url}
                x={PASSPORT_PHOTO_IMAGE_BOX.x}
                y={PASSPORT_PHOTO_IMAGE_BOX.y}
                width={PASSPORT_PHOTO_IMAGE_BOX.width}
                height={PASSPORT_PHOTO_IMAGE_BOX.height}
                preserveAspectRatio="xMidYMid slice"
                clipPath="url(#passport-final-photo-clip)"
                onError={() => {
                  setPhotoFailed(true)
                  onPhotoError?.()
                }}
              />
            </svg>
          )}
          <div className="passport-final-value passport-final-name">{petName.trim() || '—'}</div>
          <div className="passport-final-value passport-final-id">{petId}</div>
          <div className="passport-final-value passport-final-date">{issueDate}</div>
          <div className="passport-final-value passport-final-location">{location}</div>
        </div>
      </div>

      {photoReady && (!record.photo_url || photoFailed) && <p className="passport-final-error" role="alert">{labels.photoUnavailable}</p>}

      <div className="passport-final-actions">
        <button className="passport-final-download" type="button" onClick={download} disabled={!isReady || downloadBusy}>
          <Download size={18} /> {downloadBusy ? labels.downloading : labels.download}
        </button>
        <div className="passport-final-share-control">
          <button
            className="passport-final-share"
            type="button"
            onClick={toggleShareMenu}
            disabled={!isReady || shareBusy}
            aria-expanded={shareExpanded}
            aria-controls="passport-share-options"
          >
          <Share2 size={18} /> {shareBusy ? labels.sharing : labels.share}
          </button>
          <div
            className={`passport-final-share-options ${shareExpanded ? 'is-open' : ''}`}
            id="passport-share-options"
            aria-label={labels.share}
            aria-hidden={!shareExpanded}
          >
            <button className="passport-social-button is-instagram" type="button" onClick={shareInstagram} disabled={!shareExpanded || shareBusy} aria-label={labels.shareInstagram} title={labels.shareInstagram}>
              <SocialPlatformIcon platform="instagram" />
            </button>
            <button className="passport-social-button is-facebook" type="button" onClick={shareFacebook} disabled={!shareExpanded || shareBusy} aria-label={labels.shareFacebook} title={labels.shareFacebook}>
              <SocialPlatformIcon platform="facebook" />
            </button>
            <button className="passport-social-button is-kakao" type="button" onClick={shareKakao} disabled={!shareExpanded || shareBusy} aria-label={labels.shareKakao} title={labels.shareKakao}>
              <SocialPlatformIcon platform="kakao" />
            </button>
          </div>
        </div>
      </div>
      {shareMessage && shareMessage !== labels.shared && <p className="passport-final-share-message" role="status">{shareMessage}</p>}

      <section className="passport-final-soultrace">
        <div className="passport-final-soultrace-art" aria-hidden="true">
          <img src={`/home/soultrace(${language === 'ko' ? 'ko' : 'eng'})-cropped.png`} alt="" />
        </div>
        <div>
          <h2>{labels.soulTraceTitle}</h2>
          <p>{labels.soulTraceBody}</p>
          <a href="https://soultrace.pet" target="_blank" rel="noopener noreferrer">
            {labels.soulTraceCta} <ExternalLink size={15} />
          </a>
        </div>
        <Check className="passport-final-soultrace-mark" size={22} aria-hidden="true" />
      </section>
    </section>
  )
}
