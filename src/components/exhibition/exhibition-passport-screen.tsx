import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Camera,
  Check,
  Clock3,
  Globe2,
  Languages,
  LockKeyhole,
  PawPrint,
  RefreshCw,
} from 'lucide-react'
import { formatQueueNumber } from '@/lib/exhibition-queue'
import {
  createPassportToken,
  exhibitionPassportExhibitionId,
  isExhibitionPassportEnabled,
  isExhibitionPassportLocalPreview,
  reservePetPassportId,
  retrievePetPassport,
  type PassportRecord,
} from '@/lib/exhibition-passport-api'
import { passportStage, type PassportStage } from '@/lib/exhibition-passport-flow'
import { PassportPhotoStep } from './passport-photo-step'
import { PassportFinalStep } from './passport-final-step'
import {
  readPassportSession,
  savePassportSession,
  clearPassportSession,
  readPendingPassportIdReservation,
  savePendingPassportIdReservation,
  clearPendingPassportIdReservation,
  type PassportSession,
} from '@/lib/exhibition-passport-session'

type Language = 'en' | 'ko'
type PassportView = 'welcome' | 'register' | 'photo' | 'final'

const languageStorageKey = 'eternal_beam_exhibition_passport_language_v1'

const copy = {
  en: {
    brand: 'Pet Passport',
    eyebrow: 'Create Pet Passport',
    title: 'PET PASSPORT',
    subtitle: 'A JOURNEY OF LOVE BEYOND TIME',
    intro: ['Give your pet an identity.', 'Capture today, keep forever.'],
    createId: "Start",
    officialPassport: 'OFFICIAL PET PASSPORT',
    exhibitionEdition: 'EXHIBITION EDITION',
    petIdLabel: 'PET ID',
    backToPassport: 'Back',
    photoStepTitle: 'STEP BY STEP GUIDE',
    photoStepDescription: 'Follow these steps during the event to complete your Pet Passport.',
    photoWaiting: 'You pet will appear soon...',
    photoAutomatic: '',
    photoProgressTitle: 'Photo in progress',
    photoProgressBody: "Please show your Pet ID to the exhibition staff. Your pet's photo will appear here automatically once it's taken.",
    photoAddedTitle: 'Photo added',
    photoAddedBody: "Your pet's exhibition photo has been successfully added to their passport.",
    photoLinkUnavailable: 'The photo is linked but temporarily unavailable. We will keep checking.',
    photoRefresh: 'Check photo status',
    photoPetName: "PET NAME",
    photoNoName: 'YOUR PET',
    photoNameLabel: "Your pet's name",
    photoNamePlaceholder: 'e.g. Mocha',
    photoNameHint: '',
    photoPreview: "Get the Pet Passport",
    photoReveal: "Reveal the Pet's Passport",
    finalBack: 'Back to photo',
    finalTitleReady: 'YOUR PASSPORT IS READY',
    finalTitleWaiting: 'YOUR PET PASSPORT IS ALMOST READY',
    finalSubtitleReady: 'Your pet’s photo, name, and Pet ID are now together in one keepsake.',
    finalSubtitleWaiting: 'Your passport will be complete when your exhibition photo arrives.',
    finalDownload: 'Save to image',
    finalDownloading: 'Preparing download...',
    finalShare: 'Share Passport',
    finalSharing: 'Preparing share...',
    finalShareHint: 'Choose Instagram, Facebook, or KakaoTalk. The safest sharing option keeps your Passport details private.',
    finalShared: 'Passport ready to share.',
    finalShareFallback: 'Your browser downloaded the Passport image for sharing.',
    finalShareFailed: 'Sharing was cancelled or unavailable. Please download the Passport instead.',
    finalShareInstagram: 'Share to Instagram',
    finalShareFacebook: 'Share to Facebook',
    finalShareKakao: 'Share to KakaoTalk',
    finalShareInstagramFallback: 'Passport downloaded. Open Instagram to post it.',
    finalShareFacebookOpened: 'Facebook sharing opened with a safe Passport link.',
    finalShareKakaoUnavailable: 'KakaoTalk sharing is unavailable here. Use the downloaded Passport image instead.',
    finalPhotoPending: 'Photo still pending',
    finalPhotoPendingBody: 'Your photo will appear here automatically when exhibition staff captures it.',
    finalPhotoUnavailable: 'The photo is linked but temporarily unavailable. Please try again later.',
    finalEmailTitle: 'Get your pet video',
    finalEmailBody: "Get notified when your pet's AI video is ready.",
    finalEmailPlaceholder: 'Enter your email',
    finalNotify: 'Send',
    finalEmailInvalid: 'Please enter a valid email address.',
    finalEmailUnavailable: 'We could not save your email. Please try again.',
    finalEmailSaved: 'Your email preference was saved securely.',
    finalEmailSaving: 'Saving...',
    finalAiVideoConsent: 'I agree to let Eternal Beam use my pet’s photo, details, and answers to improve its AI features.',
    finalMarketingConsent: 'I’d like to receive Eternal Beam news, special offers, and updates by email (optional).',
    finalSoulTraceTitle: 'Discover More from Soul Trace',
    finalSoulTraceBody: 'Turn the moments you love into a heartfelt letter or a seasonal Polaroid.',
    finalSoulTraceCta: 'Explore Soul Trace',
    idCreatedNote: "This ID will be used at the exhibition booth to capture your pet's photo and complete their passport.",
    steps: [
      { title: 'Visit the Photo Booth', description: "Get a photo of your pet for your passport." },
      { title: 'Experience Eternal Beam', description: "See your pet come to life on screen." },
      { title: 'Discover Soul Trace', description: 'Explore Soul Trace and try its features.' },
      { title: 'Collect Your Gift', description: 'Finish the event activities and collect your gift.' },
      { title: "Get Your Pet Passport", description: "Add your pet's name and get the passport" },
    ],
    register: 'Register your pet',
    petName: 'Pet name',
    petNameHint: 'The name you would like shown on the Passport',
    email: 'Email address',
    emailHint: 'Used only for your Passport experience',
    privacy: 'I agree to the Privacy Policy and consent to the use of this information for the Pet Passport experience.',
    continue: 'Create my Pet ID',
    creating: 'Creating your Pet ID…',
    secure: 'Your details stay private. We never display your email publicly.',
    disabled: 'Pet ID creation is in preview mode. Enable the exhibition Passport feature flag to continue.',
    idTitle: 'Your Pet ID / Queue Number',
    previewId: 'Local preview ID',
    idNote: 'This is your unique number. Keep this page on your phone and show it to exhibition staff for photo capture.',
    photoPlaceholder: 'Your exhibition portrait will appear here once the staff photo is connected.',
    waitingTitle: 'Waiting for staff photo',
    waitingBody: 'Your registration is saved. When you arrive, show your Pet ID to the exhibition team so they can capture your pet’s portrait.',
    viewPassport: 'View My Passport',
    refresh: 'Check again',
    readyTitle: 'Your Passport is ready',
    readyBody: 'Your original exhibition portrait is ready to view and download.',
    photoUnavailable: 'Your photo is linked, but is temporarily unavailable. Refresh to try again.',
    back: 'Register another pet',
    localOnly: 'Keep this browser or device available. Your private retrieval token is stored locally and is never placed in the URL.',
    unavailable: 'We could not reach the Passport service. Please try again in a moment.',
    notFound: 'We could not find this Passport on the server. Please register again.',
    retry: 'Try again',
    consentError: 'Please accept the Privacy Policy to continue.',
    language: 'KR',
  },
  ko: {
    brand: 'Pet Passport',
    eyebrow: 'Pet Passport',
    title: 'PET PASSPORT',
    subtitle: '시간을 넘어 이어지는 사랑의 여정',
    intro: ['반려동물에게 특별한 아이덴티티를 선물하세요.', '오늘의 순간을 담아, 영원히 간직하세요.'],
    createId: 'Pet ID 만들기',
    idCreatedTitle: 'PET ID가 생성되었습니다',
    idCreatedSubtitle: '이제 반려동물의 여정이 시작됩니다.',
    officialPassport: '공식 PET PASSPORT',
    exhibitionEdition: 'EXHIBITION EDITION',
    petIdLabel: 'PET ID',
    copyFailed: '복사하지 못했습니다. ID를 직접 선택해 복사해 주세요.',
    continueJourney: '계속하기',
    backToPassport: '뒤로',
    photoStepTitle: '이용 방법',
    photoStepDescription: '전시를 즐기며 Pet Passport를 완성하는 과정을 따라가 보세요.',
    photoWaiting: '전시 사진을 기다리는 중...',
    photoAutomatic: '사진이 자동으로 추가됩니다.',
    photoProgressTitle: '사진 촬영 진행 중',
    photoProgressBody: 'Pet ID를 전시 스태프에게 보여 주세요. 촬영이 완료되면 반려동물 사진이 이곳에 자동으로 표시됩니다.',
    photoAddedTitle: '사진이 추가되었습니다',
    photoAddedBody: '반려동물의 전시 사진이 Passport에 성공적으로 추가되었습니다.',
    photoLinkUnavailable: '사진은 연결되었지만 잠시 사용할 수 없습니다. 계속 확인하겠습니다.',
    photoRefresh: '사진 상태 확인',
    photoPetName: '반려동물 이름',
    photoNoName: '이름 없음',
    photoNameLabel: '반려동물 이름 (선택 사항)',
    photoNamePlaceholder: '예: 모카',
    photoNameHint: '언제든 입력하거나 수정할 수 있어요.',
    photoPreview: 'Pet Passport 미리보기',
    photoReveal: 'Pet Passport 확인하기',
    finalBack: '사진으로 돌아가기',
    finalTitleReady: 'YOUR PASSPORT가 준비되었습니다',
    finalTitleWaiting: 'YOUR PET PASSPORT가 거의 완성되었습니다',
    finalSubtitleReady: '반려동물의 사진, 이름, Pet ID가 하나의 Passport에 담겼습니다.',
    finalSubtitleWaiting: '전시 사진이 도착하면 Passport가 완성됩니다.',
    finalDownload: '다운로드',
    finalDownloading: '다운로드 준비 중...',
    finalShare: 'Passport 공유',
    finalSharing: '공유 준비 중...',
    finalShareHint: 'Instagram, Facebook 또는 KakaoTalk를 선택하세요. 안전한 공유 방식으로 Passport 정보를 보호합니다.',
    finalShared: 'Passport를 공유할 준비가 되었습니다.',
    finalShareFallback: '공유할 수 있도록 Passport 이미지를 다운로드했습니다.',
    finalShareFailed: '공유가 취소되었거나 지원되지 않습니다. Passport를 다운로드해 주세요.',
    finalShareInstagram: 'Instagram으로 공유',
    finalShareFacebook: 'Facebook으로 공유',
    finalShareKakao: 'KakaoTalk으로 공유',
    finalShareInstagramFallback: 'Passport 이미지를 다운로드했습니다. Instagram에서 게시해 주세요.',
    finalShareFacebookOpened: '안전한 Passport 링크로 Facebook 공유 창을 열었습니다.',
    finalShareKakaoUnavailable: '이 환경에서는 KakaoTalk 공유를 사용할 수 없습니다. 다운로드한 Passport 이미지를 이용해 주세요.',
    finalPhotoPending: '사진을 기다리는 중입니다',
    finalPhotoPendingBody: '전시 스태프가 촬영하면 사진이 자동으로 이곳에 표시됩니다.',
    finalPhotoUnavailable: '사진이 연결되었지만 잠시 사용할 수 없습니다. 나중에 다시 시도해 주세요.',
    finalEmailTitle: 'Eternal Beam AI 영상 받기',
    finalEmailBody: '반려동물의 AI 영상이 준비되면 알려드려요.',
    finalEmailPlaceholder: '이메일 주소 (선택 사항)',
    finalNotify: '저장하기',
    finalEmailInvalid: '유효한 이메일 주소를 입력해 주세요.',
    finalEmailUnavailable: '이메일을 저장하지 못했습니다. 다시 시도해 주세요.',
    finalEmailSaved: '이메일 수신 설정이 안전하게 저장되었습니다.',
    finalEmailSaving: '저장 중...',
    finalAiVideoConsent: 'Eternal Beam AI 영상이 준비되면 이메일로 받고 싶습니다 (선택 사항).',
    finalMarketingConsent: '새 소식과 혜택을 가끔 이메일로 받고 싶습니다 (선택 사항).',
    finalSoulTraceTitle: 'Soul Trace 더 알아보기',
    finalSoulTraceBody: '소중한 순간을 진심 어린 편지나 시즌 Polaroid로 남겨 보세요.',
    finalSoulTraceCta: 'Soul Trace 둘러보기',
    idCreatedNote: '이 ID는 전시 부스에서 반려동물 사진을 촬영하고 Passport를 완성하는 데 사용됩니다.',
    steps: [
      { title: '사진 부스 방문', description: '전시 스태프가 반려동물의 특별한 사진을 촬영해 드려요.' },
      { title: 'Eternal Beam에서 만나기', description: 'Eternal Beam 부스에서 반려동물의 특별한 AI 영상을 감상해 보세요.' },
      { title: 'Soul Trace 방문', description: 'Soul Trace 부스에서 진심 어린 이야기와 시즌 추억을 만나 보세요.' },
      { title: '선물 받기', description: '전시 경험을 마친 후 특별한 선물을 받아 가세요.' },
      { title: 'Pet Passport 다운로드', description: '반려동물의 이름을 입력하고 디지털 Passport를 확인해 보세요.' },
    ],
    register: '반려동물 등록',
    petName: '반려동물 이름',
    petNameHint: 'Passport에 표시할 이름',
    email: '이메일 주소',
    emailHint: 'Pet Passport 안내에만 사용합니다',
    privacy: '개인정보처리방침에 동의하며 Pet Passport 경험을 위해 정보가 사용되는 것에 동의합니다.',
    continue: 'Pet ID 만들기',
    creating: 'Pet ID를 만드는 중…',
    secure: '입력한 정보는 안전하게 보호되며 이메일은 공개하지 않습니다.',
    disabled: 'Pet ID 생성은 미리보기 모드입니다. Exhibition Passport 기능 플래그를 켜면 계속할 수 있습니다.',
    idTitle: 'Pet ID / 대기번호',
    previewId: '로컬 미리보기 ID',
    idNote: '고유 번호입니다. 이 화면을 휴대폰에 보관하고 전시 스태프에게 보여 주세요.',
    photoPlaceholder: '전시 스태프의 사진이 연결되면 이곳에 표시됩니다.',
    waitingTitle: '전시 사진을 기다리는 중',
    waitingBody: '등록이 저장되었습니다. 전시에 도착하면 Pet ID를 전시 스태프에게 보여 주고 사진을 촬영해 주세요.',
    viewPassport: '내 Passport 보기',
    refresh: '다시 확인하기',
    readyTitle: 'Passport가 준비되었습니다',
    readyBody: '전시 사진을 확인하고 다운로드할 수 있습니다.',
    photoUnavailable: '사진은 연결되었지만 잠시 사용할 수 없습니다. 다시 확인해 주세요.',
    back: '다른 반려동물 등록',
    localOnly: '이 브라우저 또는 기기를 계속 사용해 주세요. 비공개 조회 토큰은 로컬에만 저장되며 URL에 표시되지 않습니다.',
    unavailable: 'Passport 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.',
    notFound: '서버에서 Passport를 찾을 수 없습니다. 다시 등록해 주세요.',
    retry: '다시 시도',
    consentError: '계속하려면 개인정보 이용에 동의해 주세요.',
    language: 'EN',
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
    photo_url: null,
    email_saved: session.emailSaved,
    ai_video_consent: session.aiVideoConsent,
    marketing_consent: session.marketingConsent,
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
  const localPreview = isExhibitionPassportLocalPreview()
  const featureEnabled = isExhibitionPassportEnabled() || localPreview
  const [session, setSession] = useState<PassportSession | null>(() => readPassportSession(undefined, exhibitionId))
  const [pendingIdReservation, setPendingIdReservation] = useState(() => readPendingPassportIdReservation(undefined, exhibitionId))
  const [record, setRecord] = useState<PassportRecord | null>(() => {
    const stored = readPassportSession(undefined, exhibitionId)
    return stored ? recordFromSession(stored) : null
  })
  const [petName, setPetName] = useState(() => {
    const stored = readPassportSession(undefined, exhibitionId)
    return stored?.petName || ''
  })
  const petNameRef = useRef(petName)
  const [stage, setStage] = useState<PassportStage>(() => passportStage(record))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<PassportView>(() => {
    if (typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('step') === '4' && session) return 'final'
    if (session?.photoStatus === 'WAITING_FOR_PHOTO') return 'photo'
    if (session?.photoStatus === 'PHOTO_RECEIVED') return 'photo'
    return 'welcome'
  })

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
        const merged = {
          ...fresh,
          pet_name: petNameRef.current || fresh.pet_name || session.petName || null,
        }
        setPetName(merged.pet_name || '')
        setRecord(merged)
        setStage(passportStage(merged))
        if (fresh.photo_status === 'PHOTO_RECEIVED') setView('photo')
        savePassportSession(merged, session.passportToken, undefined, session.exhibitionId)
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

  useEffect(() => {
    if ((view !== 'photo' && view !== 'final') || !session?.passportToken) return
    let active = true
    let timer: number | undefined

    const poll = async () => {
      try {
        const fresh = await retrievePetPassport(session.passportToken)
        if (!active) return
        const merged = {
          ...fresh,
          pet_name: petNameRef.current || fresh.pet_name || session.petName || null,
        }
        setPetName(merged.pet_name || '')
        setRecord(merged)
        setStage(passportStage(merged))
        savePassportSession(merged, session.passportToken, undefined, session.exhibitionId)
        if (fresh.photo_status !== 'PHOTO_RECEIVED' || !fresh.photo_url) {
          timer = window.setTimeout(poll, 5000)
        }
      } catch (pollError) {
        if (!active) return
        setError(friendlyError(pollError, t))
        timer = window.setTimeout(poll, 5000)
      }
    }

    timer = window.setTimeout(poll, 250)
    return () => {
      active = false
      if (timer !== undefined) window.clearTimeout(timer)
    }
    // The token and view define the polling session. Language changes should not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, session?.passportToken])

  const isHome = !session && view === 'welcome'

  function toggleLanguage() {
    setLanguage((previous) => (previous === 'en' ? 'ko' : 'en'))
  }

  async function createPetId() {
    if (busy || session) return
    if (!featureEnabled) {
      setError(t.disabled)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const passportToken = pendingIdReservation?.passportToken || createPassportToken()
      const pendingReservation = {
        passportToken,
        exhibitionId: exhibitionId || 'default',
      }
      savePendingPassportIdReservation(pendingReservation)
      setPendingIdReservation(pendingReservation)
      const response = await reservePetPassportId({ exhibitionId, passportToken })
      clearPendingPassportIdReservation(undefined, response.exhibition_id)
      clearPendingPassportIdReservation(undefined, exhibitionId)
      setPendingIdReservation(null)
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
      petNameRef.current = response.pet_name || ''
      setPetName(response.pet_name || '')
      setStage(passportStage(response))
      setView('photo')
    } catch (reservationError) {
      setError(friendlyError(reservationError, t))
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
      const merged = {
        ...fresh,
        pet_name: petNameRef.current || fresh.pet_name || session.petName || null,
      }
      savePassportSession(merged, session.passportToken, undefined, session.exhibitionId)
      setPetName(merged.pet_name || '')
      setRecord(merged)
      setStage(passportStage(merged))
    } catch (retrievalError) {
      setError(friendlyError(retrievalError, t))
    } finally {
      setBusy(false)
    }
  }

  function handlePetNameChange(value: string) {
    petNameRef.current = value
    setPetName(value)
    if (!session || !record) return
    const normalizedName = value.trim() || null
    const nextRecord = { ...record, pet_name: normalizedName }
    setRecord(nextRecord)
    setSession((previous) => previous ? { ...previous, petName: normalizedName } : previous)
    savePassportSession(nextRecord, session.passportToken, undefined, session.exhibitionId)
  }

  function continueFromPhoto() {
    if (session && record) {
      const normalizedName = petName.trim() || null
      const nextRecord = { ...record, pet_name: normalizedName }
      savePassportSession(nextRecord, session.passportToken, undefined, session.exhibitionId)
      setRecord(nextRecord)
      setSession((previous) => previous ? { ...previous, petName: normalizedName } : previous)
    }
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search)
      params.set('step', '4')
      window.history.pushState({}, '', `/exhibition/passport?${params.toString()}`)
    }
    setView('final')
  }

  function resetRegistration() {
    clearPassportSession(undefined, session?.exhibitionId ?? exhibitionId)
    clearPendingPassportIdReservation(undefined, exhibitionId)
    setSession(null)
    setRecord(null)
    petNameRef.current = ''
    setPetName('')
    setStage('register')
    setView('welcome')
    setError(null)
  }

  const passportViewHref = `${'/exhibition/passport/view'}${typeof window !== 'undefined' ? window.location.search : ''}`

  return (
    <main className={`passport-page ${isHome ? 'passport-page-home' : view === 'photo' ? 'passport-page-photo-step' : view === 'final' ? 'passport-page-final-step' : 'passport-page-flow'}`}>
      <header className="passport-header">
        {!isHome && view !== 'photo' && view !== 'final' && (
          <a className="passport-brand" href="/exhibition/passport" aria-label="Pet Passport home">
            <span className="passport-brand-mark" aria-hidden="true">
              <Globe2 size={28} strokeWidth={1.1} />
              <PawPrint className="passport-brand-paw" size={14} fill="currentColor" strokeWidth={1.2} />
            </span>
            <span>{t.brand}</span>
          </a>
        )}
        <button
          className="passport-language"
          type="button"
          onClick={toggleLanguage}
          aria-label={language === 'en' ? 'Switch language to Korean' : 'Switch language to English'}
        >
          <Languages size={14} aria-hidden="true" />
          <span className={language === 'en' ? 'is-active' : ''}>EN</span>
          <span className="passport-language-divider" aria-hidden="true">|</span>
          <span className={language === 'ko' ? 'is-active' : ''}>KR</span>
        </button>
      </header>

      <section className={`passport-shell ${view === 'photo' ? 'passport-photo-step-shell' : view === 'final' ? 'passport-final-shell' : ''}`}>
        {view === 'final' && session && record ? (
          <PassportFinalStep
            record={record}
            petId={formatQueueNumber(record.queue_number)}
            petName={petName}
            language={language}
            photoReady={record.photo_status === 'PHOTO_RECEIVED'}
            labels={{
              back: t.finalBack,
              titleReady: t.finalTitleReady,
              titleWaiting: t.finalTitleWaiting,
              subtitleReady: t.finalSubtitleReady,
              subtitleWaiting: t.finalSubtitleWaiting,
              download: t.finalDownload,
              downloading: t.finalDownloading,
              share: t.finalShare,
              sharing: t.finalSharing,
              shareHint: t.finalShareHint,
              shared: t.finalShared,
              shareFallback: t.finalShareFallback,
              shareFailed: t.finalShareFailed,
              shareInstagram: t.finalShareInstagram,
              shareFacebook: t.finalShareFacebook,
              shareKakao: t.finalShareKakao,
              shareInstagramFallback: t.finalShareInstagramFallback,
              shareFacebookOpened: t.finalShareFacebookOpened,
              shareKakaoUnavailable: t.finalShareKakaoUnavailable,
              photoPending: t.finalPhotoPending,
              photoPendingBody: t.finalPhotoPendingBody,
              photoUnavailable: t.finalPhotoUnavailable,
              soulTraceTitle: t.finalSoulTraceTitle,
              soulTraceBody: t.finalSoulTraceBody,
              soulTraceCta: t.finalSoulTraceCta,
            }}
            onBack={() => {
              if (typeof window !== 'undefined') window.history.pushState({}, '', `/exhibition/passport${window.location.search.replace(/([?&])step=4(&|$)/, '$1').replace(/[?&]$/, '')}`)
              setView('photo')
            }}
            onPhotoError={() => setError(t.finalPhotoUnavailable)}
          />
        ) : view === 'photo' && session && record ? (
          <PassportPhotoStep
            passportToken={session.passportToken}
            petId={formatQueueNumber(record.queue_number)}
            petName={petName}
            photoUrl={record.photo_url || null}
            photoReceived={record.photo_status === 'PHOTO_RECEIVED' && Boolean(record.photo_url)}
            photoUnavailable={record.photo_status === 'PHOTO_RECEIVED' && !record.photo_url}
            busy={busy}
            error={error}
            labels={{
              back: t.backToPassport,
              title: t.photoStepTitle,
              description: t.photoStepDescription,
              official: t.officialPassport,
              edition: t.exhibitionEdition,
              photoWaiting: t.photoWaiting,
              photoAutomatic: t.photoAutomatic,
              photoUnavailable: t.photoLinkUnavailable,
              petId: t.petIdLabel,
              petName: t.photoPetName,
              noName: t.photoNoName,
              photoProgressTitle: t.photoProgressTitle,
              photoProgressBody: t.photoProgressBody,
              photoAddedTitle: t.photoAddedTitle,
              photoAddedBody: t.photoAddedBody,
              refresh: t.photoRefresh,
              nameLabel: t.photoNameLabel,
              namePlaceholder: t.photoNamePlaceholder,
              nameHint: t.photoNameHint,
              continueWaiting: t.photoPreview,
              continueReady: t.photoReveal,
              journeySteps: t.steps,
              email: {
                title: t.finalEmailTitle,
                body: t.finalEmailBody,
                placeholder: t.finalEmailPlaceholder,
                notify: t.finalNotify,
                saving: t.finalEmailSaving,
                invalid: t.finalEmailInvalid,
                unavailable: t.finalEmailUnavailable,
                saved: t.finalEmailSaved,
                aiVideoConsent: t.finalAiVideoConsent,
                marketingConsent: t.finalMarketingConsent,
              },
            }}
            emailSaved={record.email_saved}
            aiVideoConsent={record.ai_video_consent}
            marketingConsent={record.marketing_consent}
            onBack={resetRegistration}
            onRefresh={refreshPassport}
            onPetNameChange={handlePetNameChange}
            onContinue={continueFromPhoto}
          />
        ) : (
          <>
            {isHome && <div className="passport-home-hero">
              <div className="passport-home-hero-art" aria-hidden="true" />
              <div className="passport-home-hero-copy">
                <h1 aria-label={t.title}><span>PET</span><span>PASSPORT</span></h1>
                <button className="passport-primary-button passport-home-cta" type="button" onClick={createPetId} disabled={busy}>
                  {busy ? t.creating : t.createId} <ArrowRight size={18} />
                </button>
              </div>
            </div>}

            {(session || view === 'register') && (
              <div className={`passport-experience-grid ${stage === 'ready' ? 'is-passport-ready' : ''}`}>
                {stage !== 'register' && record && (
                  <section className="passport-card passport-status-card" aria-live="polite">
                    <div className="passport-id-panel">
                      <span className="passport-overline">{t.idTitle}</span>
                      <strong>{formatQueueNumber(record.queue_number)}</strong>
                      <div className="passport-id-meta">
                        <span className="passport-id-pet">{record.pet_name}</span>
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
            )}
          </>
        )}
      </section>
    </main>
  )
}
