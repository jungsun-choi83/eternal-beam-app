import { ArrowRight, Check, ChevronRight, Mic, Volume2, Music } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { useState, useRef } from 'react'
import { VoiceRecorder } from './VoiceRecorder'

interface ThemeSelectScreenProps {
  onNavigate?: (screen: string) => void
}

const themes = [
  {
    id: 'christmas',
    nameKey: 'theme.christmas',
    gradient: 'linear-gradient(135deg, #c31432 0%, #240b36 100%)',
    emoji: '🎄',
  },
  {
    id: 'sunset',
    nameKey: 'theme.sunset',
    gradient: 'linear-gradient(135deg, #ff6b6b 0%, #feca57 100%)',
    emoji: '🌅',
  },
  {
    id: 'forest',
    nameKey: 'theme.forest',
    gradient: 'linear-gradient(135deg, #134e5e 0%, #71b280 100%)',
    emoji: '🌲',
  },
  {
    id: 'memorial',
    nameKey: 'theme.memorial',
    gradient: 'linear-gradient(135deg, #434343 0%, #000000 100%)',
    emoji: '🕊️',
  },
  {
    id: 'galaxy',
    nameKey: 'theme.galaxy',
    gradient: 'linear-gradient(135deg, #000428 0%, #004e92 100%)',
    emoji: '🌌',
  },
  {
    id: 'sakura',
    nameKey: 'theme.sakura',
    gradient: 'linear-gradient(135deg, #fbc2eb 0%, #a6c1ee 100%)',
    emoji: '🌸',
  },
  {
    id: 'ocean',
    nameKey: 'theme.ocean',
    gradient: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    emoji: '🌊',
  },
  {
    id: 'aurora',
    nameKey: 'theme.aurora',
    gradient: 'linear-gradient(135deg, #00d4ff 0%, #090979 100%)',
    emoji: '✨',
  },
]

export function ThemeSelectScreen({ onNavigate }: ThemeSelectScreenProps) {
  const { t } = useLanguage()
  const { selectedImage, selectedTheme, setSelectedTheme, audioBlob } = useImage()
  const [localTheme, setLocalTheme] = useState(selectedTheme)
  const [showVoiceRecord, setShowVoiceRecord] = useState(false)
  const [isMixing, setIsMixing] = useState(false)
  const [mixComplete, setMixComplete] = useState(false)
  const [mixedAudioUrl, setMixedAudioUrl] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  const handleThemeSelect = (themeId: string) => {
    setLocalTheme(themeId)
    setSelectedTheme(themeId)
  }

  const handleVoiceComplete = async (blob: Blob) => {
    setIsMixing(true)
    setShowVoiceRecord(false)

    await new Promise((resolve) => setTimeout(resolve, 2000))

    const url = URL.createObjectURL(blob)
    setMixedAudioUrl(url)
    setIsMixing(false)
    setMixComplete(true)
  }

  const handlePreview = () => {
    if (mixedAudioUrl && audioRef.current) {
      if (audioRef.current.paused) {
        audioRef.current.play()
      } else {
        audioRef.current.pause()
      }
    } else if (mixedAudioUrl) {
      const audio = new Audio(mixedAudioUrl)
      audioRef.current = audio
      audio.onended = () => { audioRef.current = null }
      audio.play()
    }
  }

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1
            className="font-headline text-2xl font-bold"
            style={{ color: '#2D2640' }}
          >
            {t('theme.title')}
          </h1>
          <button
            onClick={() => onNavigate?.('upload')}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          {t('theme.desc')}
        </p>
      </div>

      {/* Preview */}
      <div className="px-6 py-6">
        <div
          className="relative overflow-hidden rounded-3xl"
          style={{
            height: '280px',
            background:
              themes.find((th) => th.id === localTheme)?.gradient,
            boxShadow: '0 20px 60px rgba(0, 0, 0, 0.2)',
          }}
        >
          <div className="absolute inset-0 flex items-center justify-center">
            {selectedImage ? (
              <img
                src={selectedImage}
                alt="Preview"
                className="h-full w-full object-cover"
                style={{
                  mixBlendMode: 'screen',
                  opacity: 0.7,
                }}
              />
            ) : (
              <div className="text-8xl drop-shadow-2xl">
                {themes.find((th) => th.id === localTheme)?.emoji}
              </div>
            )}
          </div>

          {/* Hologram Effect */}
          <div
            className="absolute inset-0"
            style={{
              background:
                'linear-gradient(180deg, transparent 0%, rgba(255,255,255,0.1) 50%, transparent 100%)',
              animation: 'scan 3s linear infinite',
            }}
          />
        </div>
      </div>

      {/* Voice Recording Section */}
      <div className="px-6 py-4">
        <div className="glass rounded-2xl p-6">
          <div className="mb-4 flex items-center gap-2">
            <Mic className="h-5 w-5 text-[#9B7EBD]" />
            <h3 className="font-bold" style={{ color: '#2d3748' }}>
              음성 메시지 녹음
            </h3>
          </div>

          {!showVoiceRecord && !isMixing && !mixComplete && (
            <div className="space-y-3">
              <button
                onClick={() => setShowVoiceRecord(true)}
                className="flex w-full items-center justify-center gap-3 rounded-xl py-4 font-semibold"
                style={{
                  background: 'linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%)',
                  color: '#667eea',
                }}
              >
                <Mic className="h-6 w-6" />
                마이크로 음성 녹음하기
              </button>
              <button
                onClick={() => setMixComplete(true)}
                className="w-full text-center text-sm font-medium"
                style={{ color: '#718096' }}
              >
                녹음 건너뛰기
              </button>
            </div>
          )}

          {showVoiceRecord && (
            <VoiceRecorder onComplete={handleVoiceComplete} />
          )}

          {isMixing && (
            <div className="flex flex-col items-center py-8">
              <div className="mb-4 flex gap-1">
                {[...Array(12)].map((_, i) => (
                  <div
                    key={i}
                    className="w-1 rounded-full"
                    style={{
                      height: '32px',
                      background: 'linear-gradient(180deg, #667eea 0%, #764ba2 100%)',
                      animation: 'equalizer 0.5s ease-in-out infinite',
                      animationDelay: `${i * 0.05}s`,
                    }}
                  />
                ))}
              </div>
              <p className="text-center font-semibold" style={{ color: '#667eea' }}>
                배경음악과 믹싱 중...
              </p>
              <p className="mt-1 text-center text-sm" style={{ color: '#718096' }}>
                잠시만 기다려주세요
              </p>
            </div>
          )}

          {mixComplete && (
            <div className="flex flex-wrap items-center gap-4">
              <div
                className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full"
                style={{
                  background: 'linear-gradient(135deg, #8DD4C7 0%, #6BC4B5 100%)',
                }}
              >
                <Volume2 className="h-6 w-6 text-white" />
              </div>
              <div className="flex-1 min-w-0">
                <p className="font-semibold" style={{ color: '#2d3748' }}>
                  믹싱 완료
                </p>
                <p className="text-xs" style={{ color: '#718096' }}>
                  목소리 + 배경음악
                </p>
              </div>
              <button
                onClick={handlePreview}
                className="flex shrink-0 items-center gap-2 rounded-xl px-4 py-2 font-semibold text-white"
                style={{
                  background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                }}
              >
                <Music className="h-4 w-4" />
                미리듣기
              </button>
            </div>
          )}
        </div>
      </div>

      {mixedAudioUrl && <audio ref={audioRef} src={mixedAudioUrl} className="hidden" />}

      {/* Theme Grid */}
      <div className="px-6 pb-32 py-4">
        <div className="grid grid-cols-2 gap-4">
          {themes.map((theme) => (
            <button
              key={theme.id}
              onClick={() => handleThemeSelect(theme.id)}
              className="relative overflow-hidden rounded-2xl transition-all"
              style={{
                boxShadow:
                  localTheme === theme.id
                    ? '0 8px 24px rgba(102, 126, 234, 0.4)'
                    : '0 4px 16px rgba(0, 0, 0, 0.08)',
                transform:
                  localTheme === theme.id ? 'scale(1.05)' : 'scale(1)',
              }}
            >
              {/* Selected Badge */}
              {localTheme === theme.id && (
                <div
                  className="absolute right-2 top-2 z-10 flex h-8 w-8 items-center justify-center rounded-full"
                  style={{
                    background: 'white',
                    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.2)',
                  }}
                >
                  <Check className="h-5 w-5 text-[#667eea]" />
                </div>
              )}

              {/* Theme Preview */}
              <div
                className="flex h-32 items-center justify-center text-5xl"
                style={{
                  background: theme.gradient,
                }}
              >
                {theme.emoji}
              </div>

              {/* Theme Name */}
              <div className="bg-white p-3">
                <p
                  className="text-center text-sm font-bold"
                  style={{ color: '#2d3748' }}
                >
                  {t(theme.nameKey)}
                </p>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Bottom CTA */}
      <div
        className="fixed bottom-0 left-0 right-0 px-6 py-4"
        style={{
          background: 'rgba(255, 255, 255, 0.95)',
          backdropFilter: 'blur(20px)',
          borderTop: '1px solid rgba(0, 0, 0, 0.05)',
        }}
      >
        <button
          onClick={() => onNavigate?.('checkout')}
          disabled={!mixComplete}
          className="flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all disabled:opacity-50"
          style={{
            background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
          }}
        >
          콘텐츠 생성하기
          <ChevronRight className="h-5 w-5" />
        </button>
      </div>

      <style>{`
        @keyframes scan {
          0% { transform: translateY(-100%); }
          100% { transform: translateY(100%); }
        }
        @keyframes equalizer {
          0%, 100% { transform: scaleY(0.3); }
          50% { transform: scaleY(1); }
        }
      `}</style>
    </div>
  )
}
