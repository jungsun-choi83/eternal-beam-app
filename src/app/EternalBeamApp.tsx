import { useState, useEffect, useCallback } from 'react'
import { getWalletBalance } from '@/app/services/videoProcessingApi'
import { getEternalBeamUserId } from '@/lib/eternal-beam-user'
import {
  runCreditMotionGeneration,
  isInsufficientCreditsError,
} from '@/lib/credit-pipeline'
import { saveCreditSession } from '@/lib/credit-session'
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
import { DeviceScreen } from '@/components/memorial/device-screen'
import { SettingsScreen } from '@/components/memorial/settings-screen'
import { memorialT } from '@/components/memorial/memorial-i18n'
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
  | 'device'
  | 'settings'

const themes = [
  { id: 1, name: 'Celestial', price: '' },
  { id: 2, name: 'Golden Meadow', price: '' },
  { id: 3, name: 'Starlight', price: '' },
  { id: 4, name: 'Aurora', price: '$2.99' },
  { id: 5, name: 'Sunset', price: '$2.99' },
  { id: 6, name: 'Ocean Deep', price: '$2.99' },
]

const pageVariants = {
  initial: { opacity: 0 },
  animate: { opacity: 1 },
  exit: { opacity: 0 },
}

const pageTransition = {
  duration: 0.15,
  ease: 'easeOut' as const,
}

const filmSkipOnboarding =
  import.meta.env.VITE_FILM_SKIP_ONBOARDING === '1'

const CREDIT_COST = Number(import.meta.env.VITE_CREDIT_COST_PER_PLACE || '4')
const CREDITS_ENABLED = import.meta.env.VITE_ENABLE_CREDITS !== '0'

export function EternalBeamApp() {
  const [screen, setScreen] = useState<Screen>(
    filmSkipOnboarding ? 'home' : 'onboarding'
  )
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

  const navigateTo = (nextScreen: Screen) => {
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
      if (isInsufficientCreditsError(e)) {
        const skip = window.confirm(`${msg}\n\n${tc.skipWithoutCredit}?`)
        if (!skip) {
          setCreditBusy(false)
          return
        }
      } else {
        alert(`${tc.creditFailed}: ${msg}`)
      }
      navigateTo('preview')
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
    const theme = themes.find((t) => t.id === pendingPremiumTheme)
    return theme || { id: 0, name: 'Premium Theme', price: '$2.99' }
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
        <AnimatePresence mode="sync" initial={false}>
          {screen === 'onboarding' && (
            <motion.div
              key="onboarding"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full w-full"
              style={{ position: 'relative', display: 'block', minHeight: '100%' }}
            >
              <OnboardingScreen
                language={language}
                onComplete={() => navigateTo('signup')}
              />
            </motion.div>
          )}

          {screen === 'signup' && (
            <motion.div
              key="signup"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <AuthScreen
                language={language}
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
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <AuthScreen
                language={language}
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
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <QRConnectionScreen
                language={language}
                onComplete={() => navigateTo('home')}
                onBack={() => navigateTo('signup')}
                onSkip={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {screen === 'home' && (
            <motion.div
              key="home"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
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
                onSaveToNFC={() =>
                  cutoutImage ? navigateTo('preview') : navigateTo('photoUpload')
                }
              />
            </motion.div>
          )}

          {screen === 'gallery' && (
            <motion.div
              key="gallery"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <GalleryScreen
                onSelectItem={(id: number) => console.log('Selected item', id)}
                onAddNew={() => navigateTo('photoUpload')}
                onBack={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {screen === 'photoUpload' && (
            <motion.div
              key="photoUpload"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <PhotoUploadScreen
                uploadedImage={uploadedImage}
                language={language}
                onImageUpload={handleImageUpload}
                onContinue={() => navigateTo('aiProcessing')}
                onBack={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {screen === 'aiProcessing' && (
            <motion.div
              key="aiProcessing"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
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
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
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
                onSkip={() => navigateTo('preview')}
                onBack={() => navigateTo('home')}
              />
            </motion.div>
          )}

          {screen === 'checkout' && (
            <motion.div
              key="checkout"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <PaymentScreen
                language={language}
                selectedTheme={getPendingThemeInfo()}
                onComplete={handlePaymentComplete}
                onSkip={handlePaymentSkip}
                onBack={() => navigateTo('themeSelection')}
              />
            </motion.div>
          )}

          {screen === 'preview' && (
            <motion.div
              key="preview"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <PreviewScreen
                cutoutImage={cutoutImage}
                selectedTheme={selectedTheme}
                language={language}
                settings={previewSettings}
                onSettingsChange={handlePreviewSettingsChange}
                onComplete={() => navigateTo('nfcPlayback')}
                onBack={() => navigateTo('themeSelection')}
              />
            </motion.div>
          )}

          {screen === 'nfcPlayback' && (
            <motion.div
              key="nfcPlayback"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <NFCPlaybackScreen
                language={language}
                onComplete={handleReset}
                onBack={() => navigateTo('preview')}
              />
            </motion.div>
          )}

          {screen === 'device' && (
            <motion.div
              key="device"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <DeviceScreen
                onBack={() => navigateTo('settings')}
                onReconnect={() => navigateTo('qrConnection')}
              />
            </motion.div>
          )}

          {screen === 'settings' && (
            <motion.div
              key="settings"
              variants={pageVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={pageTransition}
              className="h-full"
            >
              <SettingsScreen
                currentLanguage={language}
                onChangeLanguage={() =>
                  handleLanguageChange(language === 'ko' ? 'en' : 'ko')
                }
                onDeviceSettings={() => navigateTo('device')}
                onBack={() => navigateTo('home')}
                onLogout={handleLogout}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </MobileFrame>
    </main>
  )
}
