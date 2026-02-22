import {
  Plus,
  Home,
  Image,
  Smartphone,
  Settings,
  Grid3x3,
  User,
  Globe,
  Film,
  Pause,
  Rocket,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useEffect, useState } from 'react'
import { subscribeToDevicePlaying } from '../../services/firebaseService'
import { getUserQuota, ensureUserQuota } from '../../services/quotaService'
import { UsageGauge } from './UsageGauge'
import { UpgradePopup } from './UpgradePopup'
import { auth } from '../../config/firebase'

const holograms = [
  { id: 1, name: '우리 강아지', theme: '크리스마스 원더', image: '🐕' },
  { id: 2, name: '여름 휴가', theme: '석양 해변', image: '🏖️' },
  { id: 3, name: '가족 사진', theme: '숲의 꿈', image: '👨‍👩‍👧‍👦' },
  { id: 4, name: '할머니', theme: '추모의 빛', image: '🕊️' },
]

interface HomeScreenProps {
  onNavigate?: (screen: string) => void
}

export function HomeScreen({ onNavigate }: HomeScreenProps) {
  const { t, language, setLanguage } = useLanguage()
  const [playingState, setPlayingState] = useState<{
    is_playing: boolean
    playback_progress?: number
  } | null>(null)
  const [quota, setQuota] = useState<Awaited<ReturnType<typeof getUserQuota>>>(null)
  const [showUpgradePopup, setShowUpgradePopup] = useState(false)

  const userId = auth.currentUser?.uid ?? 'anonymous'

  useEffect(() => {
    ensureUserQuota(userId)
      .then(() => getUserQuota(userId))
      .then(setQuota)
      .catch(() => getUserQuota(userId).then(setQuota))
  }, [userId])

  useEffect(() => {
    const deviceId = localStorage.getItem('eternal_beam_device_id')
    if (!deviceId) return

    const unsubscribe = subscribeToDevicePlaying(deviceId, (state) => {
      setPlayingState({
        is_playing: state.is_playing,
        playback_progress: state.playback_progress,
      })
    })
    return () => unsubscribe()
  }, [])

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Top Navigation */}
      <div className="glass sticky top-0 z-10 flex items-center justify-between px-6 py-4 border-0 rounded-b-2xl">
        <div className="flex items-center gap-3">
          <div className="glass-strong flex h-10 w-10 items-center justify-center rounded-full"
            style={{
              background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            }}
          >
            <User className="h-5 w-5 text-white" />
          </div>
          <div>
            <h2 className="text-sm font-bold" style={{ color: '#2d3748' }}>
              {t('home.welcome')}
            </h2>
            <p className="text-xs" style={{ color: '#718096' }}>
              {language === 'ko'
                ? '김지수'
                : language === 'en'
                  ? 'Sarah Johnson'
                  : '李娜'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full"
            onClick={() => {
              const langs: ('ko' | 'en' | 'zh')[] = ['ko', 'en', 'zh']
              const currentIndex = langs.indexOf(language)
              const nextIndex = (currentIndex + 1) % langs.length
              setLanguage(langs[nextIndex])
            }}
          >
            <div className="flex flex-col items-center justify-center">
              <Globe className="h-4 w-4 text-[#7C6B9B]" />
              <span className="text-[8px] font-bold text-[#7C6B9B]">
                {language.toUpperCase()}
              </span>
            </div>
          </button>

          <button
            onClick={() => onNavigate?.('settings')}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full"
          >
            <Settings className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
      </div>

      {/* 사용량 게이지 */}
      <div className="px-6 mb-4">
        <UsageGauge
          quota={quota}
          onUpgrade={() => setShowUpgradePopup(true)}
        />
      </div>

      {showUpgradePopup && (
        <UpgradePopup
          reason={quota?.is_over_generation ? 'generation' : 'storage'}
          onClose={() => setShowUpgradePopup(false)}
          onSelectPlan={(plan) => {
            onNavigate?.('checkout')
            setShowUpgradePopup(false)
          }}
        />
      )}

      {/* 실시간 재생 상태 표시 */}
      {playingState && (
        <div className="glass mx-6 mb-4 flex items-center gap-3 rounded-2xl px-4 py-3">
          {playingState.is_playing ? (
            <Film className="h-6 w-6 text-[#00d4ff]" />
          ) : (
            <Pause className="h-6 w-6 text-[#a0aec0]" />
          )}
          <span
            className="font-semibold"
            style={{
              color: playingState.is_playing ? '#667eea' : '#718096',
            }}
          >
            {playingState.is_playing
              ? `재생 중 🎬${playingState.playback_progress != null ? ` ${Math.round(playingState.playback_progress)}%` : ''}`
              : '대기 중 ⏸️'}
          </span>
        </div>
      )}

      {/* Brand Section - 글래스모피즘, 이터널빔 확대·아래로 */}
      <div className="glass-logo mx-6 mt-6 rounded-2xl p-8">
        <div className="mb-3">
          <div
            className="mb-1 text-xs font-bold"
            style={{
              color: '#4a5568',
              letterSpacing: '0.1em',
            }}
          >
            AIEVER
          </div>
          <div className="mb-4 text-xs" style={{ color: '#718096' }}>
            {language === 'ko'
              ? '우리는 기록하되, 판단하지 않습니다'
              : language === 'en'
                ? 'We record, not judge.'
                : '我们记录，不评判'}
          </div>
        </div>
        <img
          src="/eternal-beam-logo.png?v=2"
          alt="Eternal Beam"
          className="mb-3 h-16 w-auto max-w-[90%] object-contain md:h-20 md:max-w-[320px]"
        />
        <p className="mb-1 text-sm" style={{ color: '#4a5568' }}>
          {language === 'ko'
            ? '사라진 시간을 담는 기술'
            : language === 'en'
              ? 'Technology that captures lost time'
              : '捕捉逝去时光的技术'}
        </p>
        <p className="text-xs" style={{ color: '#718096' }}>
          {language === 'ko'
            ? '기억을 판단하지 않는 빛'
            : language === 'en'
              ? 'Light that never judges memories'
              : '不评判记忆的光芒'}
        </p>
      </div>

      {/* Hero Section - 사진/슬롯 업로드, 작게 하단으로 */}
      <div className="px-6 pt-4 pb-8 space-y-2">
        <button
          onClick={() => {
            localStorage.removeItem('eternal_beam_after_upload')
            onNavigate?.('photoUpload')
          }}
          className="glass-strong relative flex w-full items-center justify-center gap-2 overflow-hidden rounded-2xl py-3.5 text-sm font-bold text-white transition-all"
          style={{
            background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 12px 32px rgba(102, 126, 234, 0.3)',
          }}
        >
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(90deg, transparent, rgba(255,255,255,0.15), transparent)',
              animation: 'shimmer 3s ease-in-out infinite',
            }}
          />
          <Image className="relative z-10 h-4 w-4" />
          <span className="font-headline relative z-10 font-bold">
            사진 업로드 (기본 프로젝터용)
          </span>
        </button>
        <button
          onClick={() => {
            localStorage.setItem('eternal_beam_after_upload', 'themeSelection')
            onNavigate?.('themeSelection')
          }}
          className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-3.5 text-sm font-bold text-white transition-all border-2 border-purple-200/60"
          style={{
            background: 'linear-gradient(135deg, rgba(102, 126, 234, 0.9) 0%, rgba(118, 75, 162, 0.9) 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.25)',
          }}
        >
          <Grid3x3 className="h-4 w-4" />
          <span className="font-headline font-bold">슬롯 업로드 (배경화면)</span>
        </button>
      </div>

      {/* 펀딩 배너 */}
      {(import.meta.env.VITE_FUNDING_DOMESTIC_URL || import.meta.env.VITE_FUNDING_GLOBAL_URL) && (
        <div className="px-6 pb-4">
          <div className="rounded-2xl border-2 border-purple-200 bg-gradient-to-r from-purple-50 to-cyan-50 p-4">
            <p className="mb-2 text-sm font-semibold text-gray-800">펀딩으로 함께하기</p>
            <div className="flex flex-wrap gap-2">
              {import.meta.env.VITE_FUNDING_DOMESTIC_URL && (
                <a
                  href={import.meta.env.VITE_FUNDING_DOMESTIC_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl bg-purple-600 px-4 py-2 text-sm font-semibold text-white transition-all hover:bg-purple-700"
                >
                  <Rocket className="h-4 w-4" />
                  국내 펀딩
                </a>
              )}
              {import.meta.env.VITE_FUNDING_GLOBAL_URL && (
                <a
                  href={import.meta.env.VITE_FUNDING_GLOBAL_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 rounded-xl bg-cyan-600 px-4 py-2 text-sm font-semibold text-white transition-all hover:bg-cyan-700"
                >
                  <Globe className="h-4 w-4" />
                  해외 펀딩
                </a>
              )}
            </div>
          </div>
        </div>
      )}

      {/* My Holograms Section */}
      <div className="px-6 pb-24">
        <div className="mb-4 flex items-center justify-between">
          <h3
            className="text-xl font-bold"
            style={{
              fontFamily: 'var(--font-headline)',
              color: '#2d3748',
            }}
          >
            {t('home.holograms')}
          </h3>
          <button
            onClick={() => onNavigate?.('gallery')}
            className="flex items-center gap-1"
            style={{ color: '#9B7EBD' }}
          >
            <Grid3x3 className="h-4 w-4" />
            <span className="text-sm">{t('home.viewAll')}</span>
          </button>
        </div>

        {/* Grid */}
        <div className="grid grid-cols-2 gap-4">
          {holograms.map((hologram) => (
            <div
              key={hologram.id}
              className="glass overflow-hidden rounded-2xl"
            >
              <div
                className="flex h-40 items-center justify-center text-6xl"
                style={{
                  background:
                    'linear-gradient(135deg, rgba(102, 126, 234, 0.08) 0%, rgba(118, 75, 162, 0.08) 100%)',
                }}
              >
                {hologram.image}
              </div>
              <div className="p-3">
                <h4
                  className="mb-1 text-sm font-bold"
                  style={{ color: '#2d3748' }}
                >
                  {hologram.name}
                </h4>
                <p className="text-xs" style={{ color: '#718096' }}>
                  {hologram.theme}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Bottom Navigation */}
      <div className="glass fixed bottom-0 left-0 right-0 px-6 py-4 border-0 rounded-t-2xl">
        <div className="flex items-center justify-around">
          <button
            onClick={() => onNavigate?.('home')}
            className="flex flex-col items-center gap-1"
          >
            <Home className="h-6 w-6 text-[#7C6B9B]" />
            <span className="text-xs font-semibold text-[#7C6B9B]">
              {t('home.nav.home')}
            </span>
          </button>
          <button
            onClick={() => onNavigate?.('gallery')}
            className="flex flex-col items-center gap-1"
          >
            <Image className="h-6 w-6 text-[#a0aec0]" />
            <span className="text-xs text-[#a0aec0]">
              {t('home.nav.gallery')}
            </span>
          </button>
          <button
            onClick={() => onNavigate?.('device')}
            className="flex flex-col items-center gap-1"
          >
            <Smartphone className="h-6 w-6 text-[#a0aec0]" />
            <span className="text-xs text-[#a0aec0]">
              {t('home.nav.device')}
            </span>
          </button>
          <button
            onClick={() => onNavigate?.('settings')}
            className="flex flex-col items-center gap-1"
          >
            <Settings className="h-6 w-6 text-[#a0aec0]" />
            <span className="text-xs text-[#a0aec0]">
              {t('home.nav.settings')}
            </span>
          </button>
        </div>
      </div>

      {/* FAB */}
      <button
        onClick={() => onNavigate?.('upload')}
        className="fixed bottom-24 right-6 flex h-14 w-14 items-center justify-center rounded-full shadow-lg"
        style={{
          background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)',
          boxShadow: '0 8px 24px rgba(0, 212, 255, 0.4)',
        }}
      >
        <Plus className="h-6 w-6 text-white" />
      </button>

      <style>{`
        @keyframes shimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>
    </div>
  )
}
