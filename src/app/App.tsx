import { useState, useEffect, lazy, Suspense } from 'react'
import { GlassBackground } from './components/shared/GlassBackground'
import { OnboardingScreen } from './components/eternal-beam/OnboardingScreen'
import { SignupScreen } from './components/eternal-beam/SignupScreen'
import { LoginScreen } from './components/eternal-beam/LoginScreen'
import { QRConnectionScreen } from './components/eternal-beam/QRConnectionScreen'
import { DeviceConnectionScreen } from './components/eternal-beam/DeviceConnectionScreen'
import { HomeScreen } from './components/eternal-beam/HomeScreen'
import { GalleryScreen } from './components/eternal-beam/GalleryScreen'
import { UploadScreen } from './components/eternal-beam/UploadScreen'
import { PhotoUploadScreen } from './components/eternal-beam/PhotoUploadScreen'
import { AIProcessScreen } from './components/eternal-beam/AIProcessScreen'
import { AIProcessingScreen } from './components/eternal-beam/AIProcessingScreen'
import { ThemeSelectScreen } from './components/eternal-beam/ThemeSelectScreen'
import { ThemeSelectionScreen } from './components/eternal-beam/ThemeSelectionScreen'
import { RecordScreen } from './components/eternal-beam/RecordScreen'
import { StorageChoiceScreen } from './components/eternal-beam/StorageChoiceScreen'

const PreviewScreen = lazy(
  () =>
    import('./components/eternal-beam/PreviewScreen').then((m) => ({
      default: m.PreviewScreen,
    })),
)
import { QRCodeScreen } from './components/eternal-beam/QRCodeScreen'
import { NFCScreen } from './components/eternal-beam/NFCScreen'
import { PlaybackScreen } from './components/eternal-beam/PlaybackScreen'
import { CompleteScreen } from './components/eternal-beam/CompleteScreen'
import { CheckoutScreen } from './components/eternal-beam/CheckoutScreen'
import { DeviceScreen } from './components/eternal-beam/DeviceScreen'
import { SettingsScreen } from './components/eternal-beam/SettingsScreen'
import { LanguageProvider, useLanguage } from './contexts/LanguageContext'
import { ImageProvider } from './contexts/ImageContext'
import { initializeStorage } from './utils/storage'

type Screen =
  | 'onboarding'
  | 'signup'
  | 'login'
  | 'qrConnection'
  | 'deviceConnection'
  | 'home'
  | 'gallery'
  | 'upload'
  | 'photoUpload'
  | 'aiprocess'
  | 'aiProcessing'
  | 'theme'
  | 'themeSelection'
  | 'preview'
  | 'record'
  | 'storageChoice'
  | 'qrcode'
  | 'nfc'
  | 'playback'
  | 'complete'
  | 'checkout'
  | 'device'
  | 'settings'

function AppContent() {
  const [currentScreen, setCurrentScreen] = useState<Screen>('onboarding')
  const { language, setLanguage } = useLanguage()

  const navigate = (screen: string) => setCurrentScreen(screen as Screen)

  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      const screenMap: { [key: string]: Screen } = {
        '1': 'onboarding',
        '2': 'signup',
        '3': 'login',
        '4': 'qrConnection',
        '5': 'deviceConnection',
        '6': 'home',
        '7': 'upload',
        '8': 'theme',
        '9': 'checkout',
        '0': 'nfc',
        'b': 'playback',
        'B': 'playback',
        'c': 'complete',
        'C': 'complete',
        'k': 'checkout',
        'K': 'checkout',
        'd': 'device',
        'D': 'device',
        's': 'settings',
        'S': 'settings',
      }
      if (screenMap[e.key]) {
        setCurrentScreen(screenMap[e.key])
      }
    }
    window.addEventListener('keydown', handleKeyPress)
    return () => window.removeEventListener('keydown', handleKeyPress)
  }, [])

  const renderScreen = () => {
    switch (currentScreen) {
      case 'onboarding':
        return <OnboardingScreen onNavigate={navigate} />
      case 'signup':
        return <SignupScreen onNavigate={navigate} />
      case 'login':
        return <LoginScreen onNavigate={navigate} />
      case 'qrConnection':
        return <QRConnectionScreen onNavigate={navigate} />
      case 'deviceConnection':
        return <DeviceConnectionScreen onNavigate={navigate} />
      case 'home':
        return <HomeScreen onNavigate={navigate} />
      case 'gallery':
        return <GalleryScreen onNavigate={navigate} />
      case 'upload':
        return <UploadScreen onNavigate={navigate} />
      case 'photoUpload':
        return <PhotoUploadScreen onNavigate={navigate} />
      case 'aiprocess':
        return <AIProcessScreen onNavigate={navigate} />
      case 'aiProcessing':
        return <AIProcessingScreen onNavigate={navigate} />
      case 'theme':
        return <ThemeSelectScreen onNavigate={navigate} />
      case 'themeSelection':
        return <ThemeSelectionScreen onNavigate={navigate} />
      case 'preview':
        return (
          <Suspense
            fallback={
              <div
                className="flex h-full w-full items-center justify-center"
                style={{
                  background: 'linear-gradient(180deg, #0f0f23 0%, #1a1a3e 100%)',
                  color: '#fff',
                }}
              >
                로딩 중...
              </div>
            }
          >
            <PreviewScreen onNavigate={navigate} />
          </Suspense>
        )
      case 'record':
        return <RecordScreen onNavigate={navigate} />
      case 'storageChoice':
        return <StorageChoiceScreen onNavigate={navigate} />
      case 'qrcode':
        return <QRCodeScreen onNavigate={navigate} />
      case 'nfc':
        return <NFCScreen onNavigate={navigate} />
      case 'playback':
        return <PlaybackScreen onNavigate={navigate} />
      case 'complete':
        return <CompleteScreen onNavigate={navigate} />
      case 'checkout':
        return <CheckoutScreen onNavigate={navigate} />
      case 'device':
        return <DeviceScreen onNavigate={navigate} />
      case 'settings':
        return <SettingsScreen onNavigate={navigate} />
      default:
        return <OnboardingScreen onNavigate={navigate} />
    }
  }

  return (
    <div
      className="relative h-screen w-full overflow-hidden"
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(180deg, rgba(255,255,255,0.95) 0%, rgba(245,240,255,0.9) 50%, rgba(255,245,250,0.95) 100%)',
      }}
    >
      <GlassBackground />
      {/* Keyboard Shortcuts Hint */}
      <div className="fixed top-6 left-6 z-50 text-xs text-white/50 space-y-1">
        <div>1-9,0: Screens</div>
        <div>C: Complete | K: 결제(Checkout)</div>
        <div>D: Device</div>
        <div>S: Settings</div>
      </div>

      {/* Language Selector */}
      <div className="fixed right-6 top-6 z-50">
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value as 'ko' | 'en' | 'zh')}
          className="glass cursor-pointer rounded-full border border-white/30 bg-white/10 px-4 py-2 text-white backdrop-blur-md"
        >
          <option value="ko" className="bg-purple-900">
            🇰🇷 한국어
          </option>
          <option value="en" className="bg-purple-900">
            🇺🇸 English
          </option>
          <option value="zh" className="bg-purple-900">
            🇨🇳 中文
          </option>
        </select>
      </div>

      {/* Screen Navigation - Development Only */}
      {import.meta.env.DEV && (
      <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2">
        <div className="glass rounded-full border border-white/30 bg-white/10 px-6 py-3 backdrop-blur-md">
          <select
            value={currentScreen}
            onChange={(e) => setCurrentScreen(e.target.value as Screen)}
            className="cursor-pointer bg-transparent text-sm text-white outline-none"
          >
            <option value="onboarding" className="bg-purple-900">
              온보딩
            </option>
            <option value="signup" className="bg-purple-900">
              회원가입
            </option>
            <option value="login" className="bg-purple-900">
              로그인
            </option>
            <option value="qrConnection" className="bg-purple-900">
              QR 기기등록
            </option>
            <option value="deviceConnection" className="bg-purple-900">
              Wi-Fi 설정
            </option>
            <option value="home" className="bg-purple-900">
              홈
            </option>
            <option value="gallery" className="bg-purple-900">
              갤러리
            </option>
            <option value="upload" className="bg-purple-900">
              업로드
            </option>
            <option value="photoUpload" className="bg-purple-900">
              사진 업로드
            </option>
            <option value="aiProcessing" className="bg-purple-900">
              AI 처리
            </option>
            <option value="theme" className="bg-purple-900">
              테마
            </option>
            <option value="themeSelection" className="bg-purple-900">
              테마 선택
            </option>
            <option value="preview" className="bg-purple-900">
              3D 미리보기
            </option>
            <option value="record" className="bg-purple-900">
              음성 녹음
            </option>
            <option value="storageChoice" className="bg-purple-900">
              저장위치 선택
            </option>
            <option value="qrcode" className="bg-purple-900">
              QR 연결
            </option>
            <option value="nfc" className="bg-purple-900">
              NFC 슬롯
            </option>
            <option value="playback" className="bg-purple-900">
              재생
            </option>
            <option value="complete" className="bg-purple-900">
              완료
            </option>
            <option value="checkout" className="bg-purple-900">
              결제
            </option>
            <option value="device" className="bg-purple-900">
              기기 관리
            </option>
            <option value="settings" className="bg-purple-900">
              설정
            </option>
          </select>
        </div>
      </div>
      )}

      {/* Main Screen Container */}
      <div className="flex h-full w-full items-center justify-center p-8">
        <div
          className="glass overflow-hidden rounded-3xl border border-white/20 shadow-2xl"
          style={{
            width: '100%',
            maxWidth: '480px',
            height: '100%',
            maxHeight: '900px',
            borderRadius: '40px',
          }}
        >
          {renderScreen()}
        </div>
      </div>
    </div>
  )
}

export default function App() {
  useEffect(() => {
    initializeStorage()
  }, [])

  return (
    <LanguageProvider>
      <ImageProvider>
        <AppContent />
      </ImageProvider>
    </LanguageProvider>
  )
}
