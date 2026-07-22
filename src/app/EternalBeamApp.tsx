import { useState, useEffect, useRef } from 'react'
import { createDisplayCutoutUrl } from '@/lib/display-image'
import { persistDeviceContentFromPipeline } from '@/lib/persist-device-content'
import { AnimatePresence, motion } from 'framer-motion'
import { MobileFrame } from '@/components/memorial/mobile-frame'
import { OnboardingScreen } from '@/components/memorial/onboarding-screen'
import { AuthScreen } from '@/components/memorial/auth-screen'
import { QRConnectionScreen } from '@/components/memorial/qr-connection-screen'
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
} from '@/components/memorial/themes'
import { clearStoredCustomBgVideoUrl } from '@/lib/custom-background-store'
import { isForestTheme } from '@/lib/forest-demo-config'
import { isPublicForestEntry } from '@/lib/app-entry'
import {
  DEVICE_DEMO_GOYA_CUTOUT,
  isDeviceKickstarterDemo,
} from '@/lib/device-demo-config'
import { FOREST_THEME_ID } from '@/lib/forest-demo-config'
import { inferMediaKind } from '@/lib/media-file-kind'
import type { PickedMedia } from '@/lib/pick-media-file'

type Screen =
  | 'onboarding'
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
  | 'nfcPlayback'
  | 'forestExperience'
  | 'devicePlay'
  | 'device'
  | 'settings'

function resolveInitialScreen(): Screen {
  if (typeof window === 'undefined') return 'qrConnection'
  if (isPublicForestEntry()) return 'forestExperience'
  if (isDeviceKickstarterDemo()) return 'home'
  return 'qrConnection'
}

type NavDirection = 'forward' | 'back'

const pageEase = [0.22, 1, 0.36, 1] as const

const pageVariants = {
  initial: (dir: NavDirection) => ({
    opacity: 0,
    x: dir === 'forward' ? 28 : -28,
  }),
  animate: {
    opacity: 1,
    x: 0,
    transition: { duration: 0.38, ease: pageEase },
  },
  exit: (dir: NavDirection) => ({
    opacity: 0,
    x: dir === 'forward' ? -28 : 28,
    transition: {
      duration: dir === 'forward' ? 0.38 : 0.32,
      ease: pageEase,
    },
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

  const navigateTo = (nextScreen: Screen, direction: NavDirection = 'forward') => {
    navDirection.current = direction
    setTapFlash(true)
    window.setTimeout(() => setTapFlash(false), 150)
    setScreen(nextScreen)
  }

  const handleImageUpload = (imageUrl: string) => {
    setUploadedImage(imageUrl)
  }

  const applyPickedMedia = (picked: PickedMedia) => {
    const { file, kind } = picked
    const alerts = memorialT(language).alerts
    if (kind === 'video' && file.size > 100 * 1024 * 1024) {
      alert(alerts.videoSize)
      return
    }

    localStorage.setItem('eternal_beam_media_type', kind)

    if (kind === 'image') {
      const reader = new FileReader()
      reader.onload = (ev) => {
        const result = String(ev.target?.result || '')
        if (!result) return
        setUploadedImage(result)
        localStorage.setItem('eternal_beam_main_photo', result)
        localStorage.removeItem('eternal_beam_main_video_url')
        navigateTo('photoUpload')
      }
      reader.onerror = () => alert(alerts.fileType)
      reader.readAsDataURL(file)
      return
    }

    const url = URL.createObjectURL(file)
    setUploadedImage(url)
    localStorage.setItem('eternal_beam_main_video_url', url)
    localStorage.removeItem('eternal_beam_main_photo')
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
    if (deviceDemo && isForestTheme(themeId)) {
      navigateTo('devicePlay')
      return
    }
    navigateTo(isPremiumTheme(themeId) ? 'checkout' : 'preview')
  }

  const handleThemeSkip = () => {
    const themeId = freeMemorialThemes[0]?.id ?? 1
    setSelectedTheme(themeId)
    persistThemeChoice(themeId)
    if (deviceDemo && isForestTheme(themeId)) {
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
    if (qrBackTarget) {
      const target = qrBackTarget
      setQrBackTarget(null)
      navigateTo(target, 'back')
      return
    }
    navigateTo('onboarding')
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
        <AnimatePresence mode="wait" initial={false}>
          {screen === 'onboarding' && (
            <motion.div
              key="onboarding"
              custom={navDirection.current}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full w-full"
              style={{ position: 'relative', display: 'block', minHeight: '100%' }}
            >
              <OnboardingScreen
                language={language}
                onLanguageChange={handleLanguageChange}
                onComplete={() => navigateTo('signup')}
                onTryForest={
                  deviceDemo ? () => navigateTo('forestExperience') : undefined
                }
              />
            </motion.div>
          )}

          {screen === 'signup' && (
            <motion.div
              key="signup"
              custom={navDirection.current}
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
                onAuthComplete={(name?: string) => {
                  if (name) setUserName(name)
                  navigateTo('home')
                }}
              />
            </motion.div>
          )}

          {screen === 'login' && (
            <motion.div
              key="login"
              custom={navDirection.current}
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
              custom={navDirection.current}
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
              custom={navDirection.current}
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
                onLanguageChange={handleLanguageChange}
                onMediaFile={handleMediaFile}
                onGallery={() => navigateTo('gallery')}
                onSettings={() => navigateTo('settings')}
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
              custom={navDirection.current}
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
              custom={navDirection.current}
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
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'aiProcessing' && (
            <motion.div
              key="aiProcessing"
              custom={navDirection.current}
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
              custom={navDirection.current}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <ThemeSelectionScreen
                cutoutImage={cutoutImage}
                selectedTheme={selectedTheme}
                language={language}
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
              custom={navDirection.current}
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
              custom={navDirection.current}
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
              custom={navDirection.current}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <PreviewScreen
                cutoutImage={cutoutImage}
                selectedTheme={selectedTheme}
                language={language}
                settings={previewSettings}
                onSettingsChange={handlePreviewSettingsChange}
                onComplete={() => {
                  if (!selectedTheme) setSelectedTheme(1)
                  persistDeviceContentFromPipeline(selectedTheme ?? 1)
                  navigateTo('nfcPlayback')
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

          {screen === 'nfcPlayback' && (
            <motion.div
              key="nfcPlayback"
              custom={navDirection.current}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <NFCPlaybackScreen
                language={language}
                onComplete={handleReset}
                onBack={() => navigateTo('preview', 'back')}
                onGoPreview={() => navigateTo('preview')}
              />
            </motion.div>
          )}

          {screen === 'devicePlay' && (
            <motion.div
              key="devicePlay"
              custom={navDirection.current}
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              className="h-full"
            >
              <MemorialDevicePlayScreen
                cutoutImage={cutoutImage}
                language={language}
                onBack={() => navigateTo('home', 'back')}
              />
            </motion.div>
          )}

          {screen === 'forestExperience' && (
            <motion.div
              key="forestExperience"
              custom={navDirection.current}
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
              custom={navDirection.current}
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
              custom={navDirection.current}
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
                onBack={() => navigateTo('home', 'back')}
                onLogout={handleLogout}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </MobileFrame>
    </main>
  )
}
