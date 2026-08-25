import { useState, useEffect, useRef } from 'react'
import { createDisplayCutoutUrl } from '@/lib/display-image'
import { persistDeviceContentFromPipeline } from '@/lib/persist-device-content'
import { AnimatePresence, motion } from 'framer-motion'
import { MobileFrame } from '@/components/memorial/mobile-frame'
import { LanguageToggle } from '@/components/memorial/language-toggle'
import { AuthScreen } from '@/components/memorial/auth-screen'
import { QRConnectionScreen, SPLASH_FADE_MS } from '@/components/memorial/qr-connection-screen'
import { HomeScreen } from '@/components/memorial/home-screen'
import { GalleryScreen } from '@/components/memorial/gallery-screen'
import { PhotoUploadScreen } from '@/components/memorial/photo-upload-screen'
import {
  AIProcessingScreen,
  ETERNAL_BEAM_PIPELINE_KEY,
} from '@/components/memorial/ai-processing-screen'
import { ThemeSelectionScreen } from '@/components/memorial/theme-selection-screen'
import { CustomBackgroundScreen } from '@/components/memorial/custom-background-screen'
import { PaymentScreen } from '@/components/memorial/payment-screen'
import { PreviewScreen } from '@/components/memorial/preview-screen'
import { ShippingAddressScreen } from '@/components/memorial/shipping-address-screen'
import { PhysicalOrderScreen } from '@/components/memorial/physical-order-screen'
import { NFCPlaybackScreen } from '@/components/memorial/nfc-playback-screen'
import { ForestExperienceScreen } from '@/components/memorial/forest-experience-screen'
import { MemorialDevicePlayScreen } from '@/components/memorial/memorial-device-play-screen'
import { DeviceScreen } from '@/components/memorial/device-screen'
import { SettingsScreen } from '@/components/memorial/settings-screen'
import { memorialT } from '@/components/memorial/memorial-i18n'
import {
  getMemorialTheme,
  CUSTOM_PHOTO_BG_THEME_ID,
  isPremiumTheme,
  freeMemorialThemes,
  DEFAULT_THEME_ID,
} from '@/components/memorial/themes'
import { clearStoredCustomBgVideoUrl } from '@/lib/custom-background-store'
import { finalizePreviewContent } from '@/lib/finalize-preview-content'
import { scheduleThemeBackgroundSync, shouldSyncThemeToDevice } from '@/lib/device-theme-sync'
import { resolveSkipThemeId } from '@/lib/theme-skip'
import { schedulePiDiscovery } from '@/lib/pi-sensor-bridge'
import { isForestTheme } from '@/lib/forest-demo-config'
import { billingReturnEntry, isPublicForestEntry, orderReturnEntry } from '@/lib/app-entry'
import { consumeSoulTracePendingUpload } from '@/lib/soul-trace-handoff'
import {
  clearBillingReturnState,
  readBillingReturnState,
  resolveBillingReturn,
  saveBillingReturnState,
} from '@/lib/billing-return-state'
import { BillingResultScreen } from '@/components/memorial/billing-result-screen'
import {
  DEVICE_DEMO_GOYA_CUTOUT,
  isDeviceKickstarterDemo,
} from '@/lib/device-demo-config'
import { FOREST_THEME_ID } from '@/lib/forest-demo-config'
import { inferMediaKind } from '@/lib/media-file-kind'
import {
  commitMainMedia,
  resolveOriginalPhoto,
  type MediaKind,
} from '@/lib/main-media-store'
import { canEnterDevicePlay, readStoredPipeline } from '@/lib/pending-generation'
import { OrderConfirmationScreen } from '@/components/memorial/order-confirmation-screen'
import { getEternalBeamPetId } from '@/lib/pet-identity'
import { traceImage } from '@/lib/image-trace' // [IMAGE-TRACE]
import type { PickedMedia } from '@/lib/pick-media-file'

type Screen =
  | 'signup'
  | 'login'
  | 'qrConnection'
  | 'home'
  | 'gallery'
  | 'photoUpload'
  | 'aiProcessing'
  | 'themeSelection'
  | 'customBackground'
  | 'checkout'
  | 'preview'
  | 'shippingAddress'
  | 'physicalOrder'
  | 'nfcPlayback'
  | 'forestExperience'
  | 'devicePlay'
  | 'device'
  | 'settings'
  | 'billingResult'
  | 'orderResult'

function resolveInitialScreen(): Screen {
  if (typeof window === 'undefined') return 'qrConnection'
  // Toss 결제 복귀 — 전용 경로가 없으면 결제 후 첫 화면(QR)으로 떨어져
  // 사용자가 방금 낸 돈의 결과를 볼 수 없다.
  if (billingReturnEntry()) return 'billingResult'
  // 실물 주문 결제 복귀도 **앱 안에서** 받는다. 예전에는 App.tsx 가 앱 셸 밖에서
  // 가로챘고, 그 화면을 나가는 유일한 길이 루트 새로고침이었다 — 루트는 아래
  // 폴백(qrConnection)으로 떨어지므로 결제를 마친 고객이 온보딩을 다시 봤다.
  if (orderReturnEntry()) return 'orderResult'
  // Soul Trace 편지를 막 가져왔다 — 다음은 아이를 만드는 단계다.
  // **기존 Upload Pet 흐름을 그대로 쓴다**(새 화면을 만들지 않는다).
  // 표식은 한 번만 소비되므로 다음 방문부터는 평소 진입 화면으로 돌아간다.
  if (consumeSoulTracePendingUpload()) return 'photoUpload'
  if (isPublicForestEntry()) return 'forestExperience'
  if (isDeviceKickstarterDemo()) return 'home'
  // 기계 QR 진입 — 1P 로고 → 2P 회원가입 → 3P 사진 업로드 (홈은 이후 단계)
  return 'qrConnection'
}

type NavDirection = 'forward' | 'back'

const DEVICE_CONNECTED_KEY = 'eternal_beam_device_connected'


const pageEase = [0.22, 1, 0.36, 1] as const

type PageMotionCustom = { dir: NavDirection; duration: number }

const pageVariants = {
  initial: ({ dir }: PageMotionCustom) => ({
    opacity: 0,
    x: dir === 'forward' ? 28 : -28,
  }),
  animate: ({ duration }: PageMotionCustom) => ({
    opacity: 1,
    x: 0,
    transition: { duration, ease: pageEase },
  }),
  exit: ({ dir, duration }: PageMotionCustom) => ({
    opacity: 0,
    x: dir === 'forward' ? -28 : 28,
    transition: { duration, ease: pageEase },
  }),
}

function persistThemeChoice(themeId: number) {
  const th = getMemorialTheme(themeId)
  if (!th) return
  try {
    localStorage.setItem('eternal_beam_theme_key', th.themeKey)
    localStorage.setItem('eternal_beam_theme_id', String(themeId))
    localStorage.setItem('eternal_beam_background_theme_id', String(themeId))
    localStorage.setItem('eternal_beam_background_theme_name', th.nameKo || th.name)
  } catch {
    /* ignore */
  }
}

export function EternalBeamApp() {
  const [screen, setScreen] = useState<Screen>(() => resolveInitialScreen())
  const [publicForestDemo] = useState(() => isPublicForestEntry())
  const [deviceDemo] = useState(() => isDeviceKickstarterDemo())
  const [uploadedImage, setUploadedImage] = useState<string | null>(null)
  const [cutoutImage, setCutoutImage] = useState<string | null>(null)
  const [selectedTheme, setSelectedTheme] = useState<number | null>(null)
  const [previewSettings, setPreviewSettings] = useState({ scale: 1, posX: 0, posY: 0 })
  const [language, setLanguage] = useState(() => {
    if (typeof window === 'undefined') return 'ko'
    return localStorage.getItem('eternal_beam_lang') || 'ko'
  })

  const handleLanguageChange = (lang: string) => {
    setLanguage(lang)
    localStorage.setItem('eternal_beam_lang', lang)
  }
  const [userName, setUserName] = useState<string | null>(null)
  const [, setIsFirstTime] = useState(true)
  const [qrBackTarget, setQrBackTarget] = useState<Screen | null>(null)
  const [tapFlash, setTapFlash] = useState(false)
  const navDirection = useRef<NavDirection>('forward')
  const pageTransitionSec = useRef(0.38)

  const pageMotionCustom = (): PageMotionCustom => ({
    dir: navDirection.current,
    duration: pageTransitionSec.current,
  })

  // Memorial 의 '크레딧 받기' → 설정으로 이동하면서 크레딧 섹션을 강조한다.
  const [focusMembership, setFocusMembership] = useState(false)

  // 설정에서 '뒤로' 를 눌렀을 때 돌아갈 화면.
  //
  // 예전에는 설정의 뒤로가 **항상 home 으로 하드코딩**돼 있었다. 그래서 Memorial 에서
  // 설정에 들어갔다 나오면 흐름의 처음으로 튕겨 나갔고(펫·테마·위치 상태는 남아 있지만
  // 그 화면으로 돌아갈 길이 없다), 크레딧을 충전하고 돌아와 잠금 해제하는 동선이
  // 그대로 끊겼다.
  //
  // qrBackTarget 과 같은 패턴이다 — 들어온 화면을 기억했다가 그리로 돌려보낸다.
  const [settingsBackTarget, setSettingsBackTarget] = useState<Screen | null>(null)

  // ── 결제 왕복 스냅샷 ───────────────────────────────────────────────────────
  // Toss 는 결제창을 마치면 페이지를 **이동**시킨다 → React state 가 사라진다.
  // 복원 가능한 화면(펫이 이미 재생 중인 곳)에 있는 동안 계속 최신 스냅샷을
  // 남겨 두면, 결제 후 돌아왔을 때 같은 펫으로 그대로 돌아갈 수 있다.
  //
  // 결제 버튼 직전이 아니라 **화면에 있는 동안** 저장하는 이유: 결제 진입은
  // devicePlay → 설정 → 시작하기로 두 단계라, 버튼 시점에는 이미 원래 화면을
  // 떠난 뒤다. 여기서 저장하면 그 경로와 무관하게 항상 정확하다.
  useEffect(() => {
    if (screen !== 'devicePlay' && screen !== 'preview') return
    saveBillingReturnState({
      screen,
      settings: previewSettings,
      contentId: readStoredPipeline()?.content_id ?? null,
    })
  }, [screen, previewSettings])

  /** 설정 열기. 돌아갈 화면을 함께 기억한다. */
  const openSettings = (from: Screen, options?: { focusMembership?: boolean }) => {
    setSettingsBackTarget(from)
    if (options?.focusMembership) setFocusMembership(true)
    navigateTo('settings')
  }

  /** 설정에서 뒤로. 기억한 화면이 없으면 예전 동작(home)을 유지한다. */
  const handleSettingsBack = () => {
    const target = settingsBackTarget ?? 'home'
    setSettingsBackTarget(null)
    setFocusMembership(false)
    navigateTo(target, 'back')
  }

  // 세션 복원 / 토큰 갱신 / 로그아웃 구독.
  //
  // 앱을 새로 열면 supabase-js 가 저장된 세션을 복원하고 INITIAL_SESSION 을 쏜다.
  // 그때 서버가 확정한 Eternal Beam 신원을 다시 받아 로컬과 맞춘다 — 이게 없으면
  // 새로고침 후 로컬 user_id 와 서버 신원이 갈라져 지갑·자산 조회가 어긋난다.
  // 여기서는 아무것도 구매하지 않는다(조회 한 번뿐).
  useEffect(() => {
    let unsubscribe = () => {}
    void import('@/lib/supabase-auth').then((m) => {
      void m.syncEternalBeamIdentity()
      unsubscribe = m.onAuthStateChange((signedIn) => {
        if (!signedIn) return
        void import('@/lib/pet-registry-api').then((registry) =>
          registry.ensureStoredReadyPetRegistered()
        )
      })
    })
    return () => unsubscribe()
  }, [])

  useEffect(() => {
    if (!deviceDemo) return
    setCutoutImage(DEVICE_DEMO_GOYA_CUTOUT)
    setSelectedTheme(FOREST_THEME_ID)
    try {
      localStorage.setItem('eternal_beam_background_theme_id', String(FOREST_THEME_ID))
      localStorage.setItem('eternal_beam_background_theme_name', '숲속')
    } catch {
      /* ignore */
    }
  }, [deviceDemo])

  useEffect(() => {
    try {
      if (localStorage.getItem(DEVICE_CONNECTED_KEY) !== '1') return
    } catch {
      return
    }
    schedulePiDiscovery(1200)
  }, [])

  const navigateTo = (nextScreen: Screen, direction: NavDirection = 'forward') => {
    navDirection.current = direction
    if (screen === 'qrConnection' && nextScreen === 'signup' && direction === 'forward') {
      pageTransitionSec.current = SPLASH_FADE_MS / 1000
    } else {
      pageTransitionSec.current = direction === 'forward' ? 0.38 : 0.32
    }
    setTapFlash(true)
    window.setTimeout(() => setTapFlash(false), 150)
    setScreen(nextScreen)
  }

  /**
   * 업로드 확정 — **두 경로가 공유하는 단 하나의 지점.**
   *
   * 예전에는 홈 화면 선택기만 저장했고 업로드 화면은 React 상태만 갱신했다.
   * 그래서 업로드 화면으로 고른 사진은 eternal_beam_main_photo 에 들어가지
   * 않았고, 원본 배경을 읽는 화면들(테마 카드·미리보기 배경·장면 합성)은
   * 지난번 사진을 보거나 아무것도 보지 못했다.
   */
  const commitUpload = (url: string, kind: MediaKind) => {
    setUploadedImage(url)
    commitMainMedia(kind, url)
  }

  /**
   * "원본 사진 그대로" 배경에 쓰이는 **단 한 장.**
   *
   * 여기서 한 번 정하고 화면들에 내려 준다. 예전에는 화면마다 각자
   * localStorage 를 읽어서, 저장이 늦거나 실패하면 카드·미리보기·생성이 서로
   * 다른 그림을 봤다.
   */
  const originalPhoto = resolveOriginalPhoto(uploadedImage)

  const handleImageUpload = (imageUrl: string, kind: MediaKind = 'image') => {
    commitUpload(imageUrl, kind)
  }

  const applyPickedMedia = (picked: PickedMedia) => {
    const { file, kind } = picked
    const alerts = memorialT(language).alerts
    if (kind === 'video' && file.size > 100 * 1024 * 1024) {
      alert(alerts.videoSize)
      return
    }

    // [IMAGE-TRACE] 홈 화면 경로의 파일 선택 지점.
    void traceImage('file-selected (home picker)', file, 'original-upload', `kind=${kind}`)

    // 저장 로직을 여기 다시 적지 않는다 — 업로드 화면과 **같은 함수**를 쓴다.
    // 두 곳에 적혀 있었을 때 한쪽이 main_photo 를 빠뜨렸고, 그 차이가 곧
    // "원본 사진이 검게 비는" 결함이었다.
    if (kind === 'image') {
      const reader = new FileReader()
      reader.onload = (ev) => {
        const result = String(ev.target?.result || '')
        if (!result) return
        void traceImage('state:uploadedImage', result, 'original-upload') // [IMAGE-TRACE]
        commitUpload(result, 'image')
        navigateTo('photoUpload')
      }
      reader.onerror = () => alert(alerts.fileType)
      reader.readAsDataURL(file)
      return
    }

    commitUpload(URL.createObjectURL(file), 'video')
    navigateTo('themeSelection')
  }

  const handleMediaFile = (file: File) => {
    const kind = inferMediaKind(file)
    if (!kind) {
      alert(memorialT(language).alerts.fileType)
      return
    }
    applyPickedMedia({ file, kind })
  }

  const handleAIProcessingComplete = async (cutoutUrl: string) => {
    const thumb = await createDisplayCutoutUrl(cutoutUrl, 640)
    setCutoutImage(thumb)
    navigateTo('themeSelection')
  }

  const handleThemeSelect = (themeId: number) => {
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
    scheduleThemeBackgroundSync(themeId)
  }

  // "내 사진으로 나만의 배경 만들기" 카드 탭 — 일반 테마 선택과 달리 결제 전에
  // 생성 화면(customBackground)을 먼저 거친다(생성이 끝나야 미리보기/결제가 가능).
  const handleSelectCustomBackground = () => {
    navigateTo('customBackground')
  }

  const handleCustomBackgroundComplete = () => {
    setSelectedTheme(CUSTOM_PHOTO_BG_THEME_ID)
    persistThemeChoice(CUSTOM_PHOTO_BG_THEME_ID)
    navigateTo('checkout')
  }

  const handleThemeContinue = (themeId: number) => {
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
    scheduleThemeBackgroundSync(themeId)
    if (deviceDemo && isForestTheme(themeId)) {
      navigateTo('devicePlay')
      return
    }
    const theme = getMemorialTheme(themeId)
    if (
      shouldSyncThemeToDevice() &&
      theme &&
      !theme.premium &&
      !theme.requiresGeneration &&
      // 실제 idle 영상이 생기기 전에는 기기 송출로 건너뛰지 않는다 —
      // 미리보기에서 확인을 눌러야 생성이 시작된다.
      canEnterDevicePlay(readStoredPipeline(), { demo: deviceDemo })
    ) {
      navigateTo('devicePlay')
      return
    }
    navigateTo(isPremiumTheme(themeId) ? 'checkout' : 'preview')
  }

  const handleThemeSkip = () => {
    // '건너뛰기' 는 "새로 고르지 않겠다" 는 뜻이지 "고른 걸 버리겠다" 가 아니다.
    // 예전에는 무조건 freeMemorialThemes[0](fresh_forest, id 8)로 덮어써서,
    // snow_forest 를 고른 뒤 Skip 을 누르면 선택이 사라졌다(localStorage 까지).
    // 이미 유효한 선택이 있으면 그대로 유지하고, 없을 때만 기본 무료 테마를 쓴다.
    const themeId = resolveSkipThemeId(selectedTheme, {
      isValidTheme: (id) => !!getMemorialTheme(id),
      defaultThemeId: DEFAULT_THEME_ID,
    })
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
    scheduleThemeBackgroundSync(themeId)
    if (deviceDemo && isForestTheme(themeId)) {
      navigateTo('devicePlay')
      return
    }
    const theme = getMemorialTheme(themeId)
    if (
      shouldSyncThemeToDevice() &&
      theme &&
      !theme.requiresGeneration &&
      canEnterDevicePlay(readStoredPipeline(), { demo: deviceDemo })
    ) {
      navigateTo('devicePlay')
      return
    }
    navigateTo('preview')
  }

  const handlePaymentComplete = () => {
    navigateTo('preview')
  }

  const handlePaymentSkip = () => {
    navigateTo('themeSelection', 'back')
  }

  const handlePreviewSettingsChange = (settings: {
    scale: number
    posX: number
    posY: number
  }) => {
    setPreviewSettings(settings)
  }

  const handleReset = () => {
    try {
      sessionStorage.removeItem(ETERNAL_BEAM_PIPELINE_KEY)
    } catch {
      /* ignore */
    }
    clearStoredCustomBgVideoUrl()
    setScreen('home')
    setUploadedImage(null)
    setCutoutImage(null)
    setSelectedTheme(null)
    setPreviewSettings({ scale: 1, posX: 0, posY: 0 })
  }

  const handleLogout = () => {
    setIsFirstTime(true)
    setQrBackTarget(null)
    try {
      sessionStorage.removeItem(ETERNAL_BEAM_PIPELINE_KEY)
    } catch {
      /* ignore */
    }
    clearStoredCustomBgVideoUrl()
    setUploadedImage(null)
    setCutoutImage(null)
    setSelectedTheme(null)
    setPreviewSettings({ scale: 1, posX: 0, posY: 0 })
    setScreen('qrConnection')
  }

  const handleQrComplete = () => {
    try {
      localStorage.setItem(DEVICE_CONNECTED_KEY, '1')
    } catch {
      /* ignore */
    }
    schedulePiDiscovery(600)
    if (qrBackTarget) {
      const target = qrBackTarget
      setQrBackTarget(null)
      navigateTo(target, 'back')
      return
    }
    // 1P 로고 → 2P 회원가입 → 3P 사진 업로드
    navigateTo('signup')
  }

  const handleQrBack = () => {
    if (!qrBackTarget) return
    const target = qrBackTarget
    setQrBackTarget(null)
    navigateTo(target, 'back')
  }

  const getCheckoutThemeInfo = () => {
    const theme = getMemorialTheme(selectedTheme)
    return theme
      ? { id: theme.id, themeKey: theme.themeKey, name: theme.name, price: theme.price, thumb: theme.thumb }
      : { id: 0, themeKey: '', name: 'Theme', price: '' }
  }

  /**
   * 결제(성공·실패·취소) 후 복귀.
   *
   * 스냅샷이 유효하면 **같은 펫의 원래 화면**으로 돌려보낸다. 새 펫을 만들지
   * 않고, 업로드·누끼·BREATHING 생성을 다시 시작하지도 않는다 — 파이프라인과
   * 테마는 이미 저장소에 있고 화면들이 스스로 읽는다. 여기서 되살리는 것은
   * React state 가 잃어버린 두 가지(화면·펫 위치)뿐이다.
   *
   * 스냅샷이 없거나 펫이 바뀌었으면 설정 화면으로 간다 — 틀린 펫을 복원하는
   * 것보다 낫다.
   */
  const handleBillingReturn = () => {
    window.history.replaceState({}, '', '/')
    const restored = resolveBillingReturn(readBillingReturnState(), readStoredPipeline())
    clearBillingReturnState()

    if (restored) {
      setPreviewSettings(restored.settings)
      // 멤버십은 화면이 마운트되면서 PremiumAssetsProvider 가 다시 조회한다 —
      // 여기서 따로 부르지 않는다(조회 경로를 두 개로 만들지 않기 위해).
      navigateTo(restored.screen)
      return
    }
    openSettings('home', { focusMembership: true })
  }

  /**
   * 실물 주문 결제 후 — **방금 산 그 아이에게 돌아간다.**
   *
   * 고객은 이미 로그인돼 있고 canonical 펫도 이미 있다. 그런데 예전에는 확인
   * 화면을 나가는 길이 `window.location.replace('/')` 하나뿐이었고, 루트는
   * resolveInitialScreen 의 폴백인 'qrConnection'(기기 연결 → 회원가입 →
   * 사진 업로드)으로 떨어졌다. 돈을 낸 직후에 온보딩을 다시 보게 되는 것이다.
   *
   * 복원은 구독 복귀와 **같은 스냅샷**을 쓴다(billing-return-state). 결제 왕복
   * 중에 화면 상태를 붙잡아 두는 문제는 이미 그쪽에서 풀렸고, 두 벌로 만들면
   * 하나가 갱신되고 다른 하나가 잊힌다. 스냅샷은 devicePlay/preview 에 머무는
   * 동안 계속 저장되므로(위 effect), 결제 진입 경로와 무관하게 항상 최신이다.
   *
   * ── 펫을 새로 만들지 않는다 ─────────────────────────────────────────────
   * 되살리는 것은 React state 가 잃어버린 두 가지(화면·펫 위치)뿐이다.
   * 누끼·테마·파이프라인은 이미 저장소에 있고 화면들이 스스로 읽는다.
   *
   * 스냅샷이 없거나 펫이 바뀌었으면 **기념품 화면**으로 간다 — 주문을 확인할 수
   * 있는 곳이고, 고객 상태를 초기화하지 않는다. 온보딩으로는 절대 보내지 않는다.
   */
  const handleOrderReturn = () => {
    window.history.replaceState({}, '', '/')
    const restored = resolveBillingReturn(readBillingReturnState(), readStoredPipeline())
    clearBillingReturnState()

    if (restored) {
      setPreviewSettings(restored.settings)
      navigateTo(restored.screen)
      return
    }
    navigateTo('physicalOrder')
  }

  // Toss 결제 복귀는 **앱 셸 밖에서** 처리한다. 결제 결과는 업로드·테마 등 어떤
  // 진행 상태와도 무관하고, 여기서 애니메이션·파이프라인 복원을 태울 이유가 없다.
  if (screen === 'orderResult') {
    return (
      <main className="min-h-[100dvh] bg-[#0a0a0a] flex items-stretch md:items-center justify-center p-0 md:p-4 overflow-hidden">
        <OrderConfirmationScreen
          onContinue={handleOrderReturn}
          onViewOrders={() => {
            window.history.replaceState({}, '', '/')
            clearBillingReturnState()
            navigateTo('physicalOrder')
          }}
        />
      </main>
    )
  }

  if (screen === 'billingResult') {
    return (
      <main className="min-h-[100dvh] bg-[#0a0a0a] flex items-stretch md:items-center justify-center p-0 md:p-4 overflow-hidden">
        <BillingResultScreen
          outcome={billingReturnEntry() === 'fail' ? 'fail' : 'success'}
          language={language}
          onContinue={handleBillingReturn}
        />
      </main>
    )
  }

  return (
    <main className="min-h-[100dvh] bg-[#0a0a0a] flex items-stretch md:items-center justify-center p-0 md:p-4 overflow-hidden">
      <div
        className="fixed inset-0 pointer-events-none overflow-hidden"
        style={{
          background:
            'radial-gradient(circle at 35% 40%, rgba(201, 162, 39, 0.05) 0%, transparent 55%)',
        }}
      />

      <MobileFrame>
        {tapFlash ? <div className="eb-tap-flash absolute inset-0 z-[80] rounded-[inherit]" aria-hidden /> : null}
        <div className="pointer-events-none absolute inset-x-0 top-[max(0.625rem,env(safe-area-inset-top,0px))] z-[60] flex justify-end px-4">
          {screen !== 'qrConnection' ? (
            <LanguageToggle
              language={language}
              onChange={handleLanguageChange}
              className="pointer-events-auto"
            />
          ) : null}
        </div>
        <AnimatePresence mode="wait" initial={false}>
          {screen === 'signup' && (
            <motion.div
              key="signup"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AuthScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                initialMode="signup"
                lockMode="signup"
                onAuthComplete={(name?: string) => {
                  if (name) setUserName(name)
                  navigateTo('photoUpload')
                }}
              />
            </motion.div>
          )}

          {screen === 'login' && (
            <motion.div
              key="login"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AuthScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                initialMode="login"
                onAuthComplete={(name?: string) => {
                  if (name) setUserName(name)
                  navigateTo('home')
                }}
              />
            </motion.div>
          )}

          {screen === 'qrConnection' && (
            <motion.div
              key="qrConnection"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <QRConnectionScreen
                language={language}
                showBack={qrBackTarget !== null}
                onComplete={handleQrComplete}
                onBack={handleQrBack}
                onSkip={handleQrComplete}
              />
            </motion.div>
          )}

          {screen === 'home' && (
            <motion.div
              key="home"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <HomeScreen
                cutoutImage={cutoutImage}
                userName={userName ?? undefined}
                language={language}
                onMediaFile={handleMediaFile}
                onGallery={() => navigateTo('gallery')}
                onSettings={() => openSettings('home')}
                onTryForest={
                  deviceDemo ? () => navigateTo('forestExperience') : undefined
                }
                onSaveToNFC={() => {
                  if (deviceDemo && cutoutImage) {
                    setSelectedTheme(FOREST_THEME_ID)
                    navigateTo('devicePlay')
                    return
                  }
                  if (!selectedTheme) setSelectedTheme(1)
                  cutoutImage ? navigateTo('preview') : navigateTo('photoUpload')
                }}
              />
            </motion.div>
          )}

          {screen === 'gallery' && (
            <motion.div
              key="gallery"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <GalleryScreen
                onSelectItem={(id: number) => console.log('Selected item', id)}
                onAddNew={() => navigateTo('photoUpload')}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'photoUpload' && (
            <motion.div
              key="photoUpload"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PhotoUploadScreen
                uploadedImage={uploadedImage}
                language={language}
                onImageUpload={handleImageUpload}
                onContinue={() => navigateTo('aiProcessing')}
                onBack={() => navigateTo('signup', 'back')}
              />
            </motion.div>
          )}

          {screen === 'aiProcessing' && (
            <motion.div
              key="aiProcessing"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <AIProcessingScreen
                uploadedImage={uploadedImage}
                language={language}
                onComplete={handleAIProcessingComplete}
              />
            </motion.div>
          )}

          {screen === 'themeSelection' && (
            <motion.div
              key="themeSelection"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ThemeSelectionScreen
                cutoutImage={cutoutImage}
                // 카드·큰 미리보기·생성이 **같은 한 장**을 쓰게 한다.
                originalPhoto={originalPhoto}
                selectedTheme={selectedTheme}
                language={language}
                deviceLinked={shouldSyncThemeToDevice()}
                onSelectTheme={handleThemeSelect}
                onSelectCustomBackground={handleSelectCustomBackground}
                onContinue={handleThemeContinue}
                onSkip={handleThemeSkip}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'customBackground' && (
            <motion.div
              key="customBackground"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <CustomBackgroundScreen
                uploadedImage={uploadedImage}
                language={language}
                onComplete={handleCustomBackgroundComplete}
                onBack={() => navigateTo('themeSelection', 'back')}
              />
            </motion.div>
          )}

          {screen === 'checkout' && (
            <motion.div
              key="checkout"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PaymentScreen
                language={language}
                selectedTheme={getCheckoutThemeInfo()}
                onComplete={handlePaymentComplete}
                onSkip={handlePaymentSkip}
                onBack={() => navigateTo('themeSelection', 'back')}
              />
            </motion.div>
          )}

          {screen === 'preview' && (
            <motion.div
              key="preview"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PreviewScreen
                cutoutImage={cutoutImage}
                originalPhoto={originalPhoto}
                selectedTheme={selectedTheme}
                language={language}
                settings={previewSettings}
                deliveryMode={
                  selectedTheme && isPremiumTheme(selectedTheme) ? 'shipping' : 'device'
                }
                onSettingsChange={handlePreviewSettingsChange}
                onComplete={() => {
                  if (!selectedTheme) setSelectedTheme(1)
                  if (selectedTheme && isPremiumTheme(selectedTheme)) {
                    navigateTo('shippingAddress')
                    return
                  }
                  // PreviewScreen 은 생성이 끝난 뒤에만 onComplete 을 부르지만,
                  // 기기 송출 진입은 여기서도 한 번 더 확인한다.
                  if (!canEnterDevicePlay(readStoredPipeline(), { demo: deviceDemo })) return
                  navigateTo('devicePlay')
                }}
                onBack={() =>
                  navigateTo(
                    selectedTheme && isPremiumTheme(selectedTheme) ? 'checkout' : 'themeSelection',
                    'back'
                  )
                }
              />
            </motion.div>
          )}

          {screen === 'shippingAddress' && (
            <motion.div
              key="shippingAddress"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ShippingAddressScreen
                language={language}
                onComplete={async () => {
                  if (selectedTheme != null) {
                    try {
                      await finalizePreviewContent(selectedTheme, previewSettings)
                    } catch {
                      persistDeviceContentFromPipeline(selectedTheme)
                    }
                  }
                  navigateTo('nfcPlayback')
                }}
                onBack={() => navigateTo('preview', 'back')}
              />
            </motion.div>
          )}

          {screen === 'physicalOrder' && (
            <motion.div
              key="physicalOrder"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              {/* 실물 구매. **펫도 편지도 여기서 만들지 않는다** — 이미 있는
                  canonical petId 와 연결된 Soul Trace 편지를 가리킬 뿐이다.
                  편지가 없으면 화면이 "먼저 연결하세요"로 막는다. */}
              <PhysicalOrderScreen
                petId={getEternalBeamPetId(readStoredPipeline()?.content_id ?? null)}
                onBack={() => navigateTo('devicePlay', 'back')}
              />
            </motion.div>
          )}

          {screen === 'nfcPlayback' && (
            <motion.div
              key="nfcPlayback"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <NFCPlaybackScreen
                language={language}
                premiumPhysical
                onComplete={handleReset}
                onBack={() => navigateTo('shippingAddress', 'back')}
                onGoPreview={() => navigateTo('preview')}
              />
            </motion.div>
          )}

          {screen === 'devicePlay' && (
            <motion.div
              key="devicePlay"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <MemorialDevicePlayScreen
                cutoutImage={cutoutImage}
                selectedTheme={selectedTheme}
                settings={previewSettings}
                language={language}
                onBack={() => navigateTo('preview', 'back')}
                onComplete={handleReset}
                onOpenKeepsakes={() => navigateTo('physicalOrder')}
                onOpenMembership={() => openSettings('devicePlay', { focusMembership: true })}
              />
            </motion.div>
          )}

          {screen === 'forestExperience' && (
            <motion.div
              key="forestExperience"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ForestExperienceScreen
                language={language}
                publicDemo={publicForestDemo}
                onBack={() =>
                  publicForestDemo
                    ? navigateTo('home', 'back')
                    : cutoutImage
                      ? navigateTo('themeSelection', 'back')
                      : navigateTo('home', 'back')
                }
                onComplete={() =>
                  deviceDemo ? navigateTo('themeSelection') : navigateTo('nfcPlayback')
                }
              />
            </motion.div>
          )}

          {screen === 'device' && (
            <motion.div
              key="device"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <DeviceScreen
                onBack={() => navigateTo('settings', 'back')}
                onReconnect={() => {
                  setQrBackTarget('device')
                  navigateTo('qrConnection')
                }}
              />
            </motion.div>
          )}

          {screen === 'settings' && (
            <motion.div
              key="settings"
              custom={pageMotionCustom()}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <SettingsScreen
                currentLanguage={language}
                onChangeLanguage={() =>
                  handleLanguageChange(language === 'ko' ? 'en' : 'ko')
                }
                onDeviceSettings={() => navigateTo('device')}
                onBack={handleSettingsBack}
                onLogout={handleLogout}
                focusMembership={focusMembership}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </MobileFrame>
    </main>
  )
}
