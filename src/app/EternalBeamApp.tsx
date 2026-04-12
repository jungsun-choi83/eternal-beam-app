import { useState } from 'react'
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
  initial: { opacity: 0, y: 20 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -20 },
}

const pageTransition = {
  duration: 0.4,
  ease: [0.25, 0.46, 0.45, 0.94],
}

export function EternalBeamApp() {
  const [screen, setScreen] = useState<Screen>('onboarding')
  const [uploadedImage, setUploadedImage] = useState<string | null>(null)
  const [cutoutImage, setCutoutImage] = useState<string | null>(null)
  const [selectedTheme, setSelectedTheme] = useState<number | null>(null)
  const [pendingPremiumTheme, setPendingPremiumTheme] = useState<number | null>(null)
  const [previewSettings, setPreviewSettings] = useState({ scale: 1, posX: 0, posY: 0 })
  const [language, setLanguage] = useState('en')
  const [userName, setUserName] = useState<string | null>(null)
  const [, setIsFirstTime] = useState(true)

  const navigateTo = (nextScreen: Screen) => {
    setScreen(nextScreen)
  }

  const handleImageUpload = (imageUrl: string) => {
    setUploadedImage(imageUrl)
  }

  const handleAIProcessingComplete = (cutoutUrl: string) => {
    setCutoutImage(cutoutUrl)
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
    <main className="min-h-screen bg-[#0a0a0a] flex items-center justify-center p-4 overflow-hidden">
      {/* Premium Ambient Background */}
      <div className="fixed inset-0 pointer-events-none overflow-hidden">
        <motion.div
          className="absolute top-1/3 left-1/3 w-[600px] h-[600px] rounded-full"
          style={{
            background:
              'radial-gradient(circle, rgba(201, 162, 39, 0.06) 0%, transparent 70%)',
            filter: 'blur(100px)',
          }}
          animate={{ scale: [1, 1.2, 1], opacity: [0.3, 0.5, 0.3] }}
          transition={{ duration: 8, repeat: Infinity, ease: 'easeInOut' }}
        />
      </div>

      <MobileFrame>
        <AnimatePresence mode="wait">
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
              <OnboardingScreen onComplete={() => navigateTo('signup')} />
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
                initialMode="signup"
                onAuthComplete={(name) => {
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
                initialMode="login"
                onAuthComplete={(name) => {
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
                onLanguageChange={setLanguage}
                onUploadPhoto={() => navigateTo('photoUpload')}
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
                onSelectItem={(id) => console.log('Selected item', id)}
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
                onSelectTheme={handleThemeSelect}
                onSelectPremiumTheme={handlePremiumThemeSelect}
                onContinue={() => navigateTo('preview')}
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
                onChangeLanguage={() => {}}
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
