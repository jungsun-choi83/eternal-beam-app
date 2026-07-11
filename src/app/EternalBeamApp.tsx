import { useState, useEffect, useCallback, useRef } from 'react'
import { getWalletBalance } from '@/app/services/videoProcessingApi'
import { getEternalBeamUserId } from '@/lib/eternal-beam-user'
import {
  runCreditMotionGeneration,
  isInsufficientCreditsError,
} from '@/lib/credit-pipeline'
import { saveCreditSession } from '@/lib/credit-session'
import { persistDeviceContentFromPipeline } from '@/lib/persist-device-content'
import { createDisplayCutoutUrl } from '@/lib/display-image'
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
import { PaymentScreen } from '@/components/memorial/payment-screen'
import { PreviewScreen } from '@/components/memorial/preview-screen'
import { NFCPlaybackScreen } from '@/components/memorial/nfc-playback-screen'
import { ForestExperienceScreen } from '@/components/memorial/forest-experience-screen'
import { DeviceScreen } from '@/components/memorial/device-screen'
import { SettingsScreen } from '@/components/memorial/settings-screen'
import { memorialT } from '@/components/memorial/memorial-i18n'
import { memorialThemes, getMemorialTheme } from '@/components/memorial/themes'
import { isForestTheme } from '@/lib/forest-demo-config'
import { isPublicForestEntry } from '@/lib/app-entry'
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
  | 'checkout'
  | 'preview'
  | 'nfcPlayback'
  | 'forestExperience'
  | 'device'
  | 'settings'

function resolveInitialScreen(): Screen {
  if (typeof window === 'undefined') return 'onboarding'
  if (isPublicForestEntry()) return 'forestExperience'
  return 'onboarding'
}

const themes = memorialThemes.map((t) => ({
  id: t.id,
  name: t.name,
  price: t.price,
}))

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

const CREDIT_COST = Number(import.meta.env.VITE_CREDIT_COST_PER_PLACE || '4')
const CREDITS_ENABLED = import.meta.env.VITE_ENABLE_CREDITS !== '0'

export function EternalBeamApp() {
  const [screen, setScreen] = useState<Screen>(resolveInitialScreen)
  const [publicForestDemo] = useState(() => isPublicForestEntry())
  const [uploadedImage, setUploadedImage] = useState<string | null>(null)
  const [cutoutImage, setCutoutImage] = useState<string | null>(null)
  const [selectedTheme, setSelectedTheme] = useState<number | null>(null)
  const [pendingPremiumTheme, setPendingPremiumTheme] = useState<number | null>(null)
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
  const [walletCredits, setWalletCredits] = useState<number | null>(null)
  const [creditBusy, setCreditBusy] = useState(false)
  const [tapFlash, setTapFlash] = useState(false)
  const navDirection = useRef<NavDirection>('forward')

  const refreshWallet = useCallback(async () => {
    if (!CREDITS_ENABLED) return
    try {
      const w = await getWalletBalance(getEternalBeamUserId(userName))
      setWalletCredits(w.current_credits)
    } catch {
      setWalletCredits(null)
    }
  }, [userName])

  useEffect(() => {
    if (screen === 'themeSelection') void refreshWallet()
  }, [screen, refreshWallet])

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
    const thumb = await createDisplayCutoutUrl(cutoutUrl, 512)
    setCutoutImage(thumb)
    navigateTo('themeSelection')
  }

  const handleThemeSelect = (themeId: number) => {
    setSelectedTheme(themeId)
  }

  const handlePremiumThemeSelect = (themeId: number) => {
    setSelectedTheme(themeId)
    setPendingPremiumTheme(null)
    navigateTo('preview')
  }

  const handlePaymentComplete = () => {
    if (pendingPremiumTheme) {
      setSelectedTheme(pendingPremiumTheme)
      setPendingPremiumTheme(null)
    }
    navigateTo('preview')
  }

  const handleThemeContinueWithCredit = async () => {
    const tc = memorialT(language).theme
    if (!selectedTheme) return
    if (!cutoutImage) {
      alert(tc.cutoutMissing)
      return
    }
    if (isForestTheme(selectedTheme)) {
      navigateTo('forestExperience')
      return
    }
    if (!CREDITS_ENABLED) {
      navigateTo('preview')
      return
    }

    setCreditBusy(true)
    try {
      let contentId: string | undefined
      try {
        const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY)
        if (raw) contentId = JSON.parse(raw).content_id
      } catch {
        /* ignore */
      }

      const userId = getEternalBeamUserId(userName)
      const { result, petImageUrl } = await runCreditMotionGeneration({
        userId,
        cutoutDisplay: cutoutImage,
        themeId: selectedTheme,
        contentId,
      })

      saveCreditSession(result, petImageUrl)
      setWalletCredits(result.credits_remaining)

      try {
        const raw = sessionStorage.getItem(ETERNAL_BEAM_PIPELINE_KEY)
        if (raw) {
          const p = JSON.parse(raw)
          p.dog_only_nobg_url = petImageUrl
          sessionStorage.setItem(ETERNAL_BEAM_PIPELINE_KEY, JSON.stringify(p))
        }
      } catch {
        /* ignore */
      }

      navigateTo('preview')
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      alert(isInsufficientCreditsError(e) ? msg : `${tc.creditFailed}: ${msg}`)
    } finally {
      setCreditBusy(false)
    }
  }

  const handlePaymentSkip = () => {
    setPendingPremiumTheme(null)
    navigateTo('themeSelection')
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
    setScreen('home')
    setUploadedImage(null)
    setCutoutImage(null)
    setSelectedTheme(null)
    setPendingPremiumTheme(null)
    setPreviewSettings({ scale: 1, posX: 0, posY: 0 })
  }

  const handleLogout = () => {
    setIsFirstTime(true)
    setScreen('onboarding')
    handleReset()
  }

  const getPendingThemeInfo = () => {
    const theme = getMemorialTheme(pendingPremiumTheme)
    return theme
      ? { id: theme.id, name: theme.name, price: theme.price }
      : { id: 0, name: 'Premium Theme', price: '' }
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
                onTryForest={() => navigateTo('forestExperience')}
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
                  navigateTo('qrConnection')
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
                onComplete={() => navigateTo('home')}
                onBack={() => navigateTo('signup', 'back')}
                onSkip={() => navigateTo('home')}
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
                onTryForest={() => navigateTo('forestExperience')}
                onSaveToNFC={() => {
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
                walletCredits={walletCredits}
                creditCost={CREDIT_COST}
                creditBusy={creditBusy}
                onSelectTheme={handleThemeSelect}
                onSelectPremiumTheme={handlePremiumThemeSelect}
                onContinue={() => void handleThemeContinueWithCredit()}
                onBack={() => navigateTo('home', 'back')}
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
                selectedTheme={getPendingThemeInfo()}
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
                onBack={() => navigateTo('themeSelection', 'back')}
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
                onComplete={() => navigateTo('nfcPlayback')}
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
                onReconnect={() => navigateTo('qrConnection')}
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
