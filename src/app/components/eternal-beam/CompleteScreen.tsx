import {
  CheckCircle2,
  Home,
  Share2,
  Sparkles,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'

interface CompleteScreenProps {
  onNavigate?: (screen: string) => void
}

export function CompleteScreen({ onNavigate }: CompleteScreenProps) {
  const { t } = useLanguage()

  return (
    <div className="relative h-full w-full overflow-hidden bg-transparent">
      {/* Confetti Effect */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        {[...Array(50)].map((_, i) => (
          <div
            key={i}
            className="absolute h-2 w-2 rounded-full"
            style={{
              left: `${Math.random() * 100}%`,
              top: '-10px',
              background:
                ['#667eea', '#764ba2', '#00d4ff'][
                  Math.floor(Math.random() * 3)
                ],
              animation: `fall ${2 + Math.random() * 3}s linear infinite`,
              animationDelay: `${Math.random() * 3}s`,
            }}
          />
        ))}
      </div>

      {/* Main Content */}
      <div className="relative z-10 flex h-full flex-col items-center justify-center px-6">
        {/* Success Icon */}
          <div className="glass-strong relative mb-8 flex h-32 w-32 items-center justify-center rounded-full"
          style={{
            background:
              'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 20px 60px rgba(155, 126, 189, 0.4)',
          }}
        >
          <CheckCircle2 className="h-16 w-16 text-white" />

          {[...Array(8)].map((_, i) => (
            <Sparkles
              key={i}
              className="absolute h-6 w-6 text-yellow-400"
              style={{
                top: '50%',
                left: '50%',
                transform: `translate(-50%, -50%) rotate(${i * 45}deg) translateY(-60px)`,
                animation: `sparkle 2s ease-in-out infinite`,
                animationDelay: `${i * 0.2}s`,
              }}
            />
          ))}
        </div>

        {/* Title */}
        <h1
          className="font-headline mb-4 text-center text-4xl font-bold"
          style={{
            background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
            backgroundClip: 'text',
          }}
        >
          {t('complete.title')}
        </h1>

        {/* Description */}
        <p className="mb-12 max-w-xs text-center" style={{ color: '#718096' }}>
          {t('complete.desc')}
        </p>

        {/* Summary Cards */}
        <div className="mb-8 w-full max-w-sm space-y-3">
          <div className="glass flex items-center gap-4 rounded-2xl p-4">
            <div className="text-4xl">🎨</div>
            <div className="flex-1">
              <h3 className="mb-1 font-bold" style={{ color: '#2d3748' }}>
                크리스마스 원더
              </h3>
              <p className="text-xs" style={{ color: '#718096' }}>
                테마 적용됨
              </p>
            </div>
          </div>

          <div className="glass flex items-center gap-4 rounded-2xl p-4">
            <div className="text-4xl">🎙️</div>
            <div className="flex-1">
              <h3 className="mb-1 font-bold" style={{ color: '#2d3748' }}>
                음성 메시지
              </h3>
              <p className="text-xs" style={{ color: '#718096' }}>
                0:32 녹음 완료
              </p>
            </div>
          </div>

          <div className="glass flex items-center gap-4 rounded-2xl p-4">
            <div className="text-4xl">📱</div>
            <div className="flex-1">
              <h3 className="mb-1 font-bold" style={{ color: '#2d3748' }}>
                Eternal Beam Device
              </h3>
              <p className="text-xs" style={{ color: '#718096' }}>
                #EB12345에 전송됨
              </p>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="w-full max-w-sm space-y-3">
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all"
            style={{
              background:
                'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
              boxShadow: '0 8px 24px rgba(155, 126, 189, 0.3)',
            }}
          >
            <Home className="h-5 w-5" />
            {t('complete.home')}
          </button>

          <button
            className="flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold transition-all"
            style={{
              background: 'white',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
              color: '#2d3748',
            }}
          >
            <Share2 className="h-5 w-5 text-[#667eea]" />
            {t('complete.share')}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes fall {
          to { transform: translateY(100vh) rotate(360deg); }
        }
        @keyframes sparkle {
          0%, 100% { opacity: 0; transform: translate(-50%, -50%) rotate(var(--r, 0deg)) translateY(-60px) scale(0); }
          50% { opacity: 1; transform: translate(-50%, -50%) rotate(var(--r, 0deg)) translateY(-80px) scale(1); }
        }
      `}</style>
    </div>
  )
}
