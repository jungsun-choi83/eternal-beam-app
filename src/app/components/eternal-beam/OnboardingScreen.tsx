import {
  Camera,
  Sparkles,
  Palette,
  Smartphone,
  ChevronRight,
} from 'lucide-react'
import { useState } from 'react'
import { useLanguage } from '../../contexts/LanguageContext'

interface OnboardingScreenProps {
  onNavigate?: (screen: string) => void
}

export function OnboardingScreen({ onNavigate }: OnboardingScreenProps) {
  const [currentSlide, setCurrentSlide] = useState(0)
  const [logoError, setLogoError] = useState(false)
  const { t } = useLanguage()

  const onboardingSlides = [
    {
      icon: Camera,
      titleKey: 'onboarding.upload.title',
      descKey: 'onboarding.upload.desc',
    },
    {
      icon: Sparkles,
      titleKey: 'onboarding.ai.title',
      descKey: 'onboarding.ai.desc',
    },
    {
      icon: Palette,
      titleKey: 'onboarding.theme.title',
      descKey: 'onboarding.theme.desc',
    },
    {
      icon: Smartphone,
      titleKey: 'onboarding.display.title',
      descKey: 'onboarding.display.desc',
    },
  ]

  const nextSlide = () => {
    if (currentSlide < onboardingSlides.length - 1) {
      setCurrentSlide(currentSlide + 1)
    } else {
      onNavigate?.('signup')
    }
  }

  const skip = () => {
    setCurrentSlide(onboardingSlides.length - 1)
  }

  const slide = onboardingSlides[currentSlide]
  const Icon = slide.icon

  return (
    <div className="relative flex h-full w-full flex-col overflow-hidden bg-transparent">
      {/* Skip */}
      {currentSlide < onboardingSlides.length - 1 && (
        <button
          onClick={skip}
          className="absolute right-6 top-6 z-10 px-4 py-2 text-sm font-semibold"
          style={{ color: '#9B7EBD' }}
        >
          {t('onboarding.skip')}
        </button>
      )}

      {/* 메인 타이틀: Eternal Beam 로고 - 사각형 없이, 나의 영상올리기 폰트 크기 */}
      <div className="relative z-10 mx-6 mt-20 flex flex-col items-center">
        {logoError ? (
          <h1
            className="font-headline mb-2 text-center text-3xl font-bold tracking-tight md:text-4xl"
            style={{ color: '#2D2640' }}
          >
            Eternal Beam
          </h1>
        ) : (
          <img
            src="/eternal-beam-logo.png?v=4"
            alt="Eternal Beam"
            className="mb-2 w-auto object-contain"
            style={{
              height: 80,
              minHeight: 80,
              maxWidth: 'min(400px, 92vw)',
            }}
            onError={() => setLogoError(true)}
          />
        )}
        <p
          className="text-center text-sm font-medium"
          style={{ color: '#8B7A9E' }}
        >
          당신의 영원한 순간을 빛으로
        </p>
      </div>

      {/* 슬라이드 콘텐츠 */}
      <div className="relative z-10 flex flex-1 flex-col items-center justify-center px-8">
        <div className="glass-logo relative mb-6 flex h-24 w-24 items-center justify-center rounded-full">
          <Icon className="h-12 w-12" style={{ color: '#7C6B9B' }} />
        </div>

        <h2
          className="font-headline mb-3 px-4 text-center text-xl font-bold"
          style={{ color: '#2D2640' }}
        >
          {t(slide.titleKey)}
        </h2>

        <p
          className="max-w-xs text-center text-sm leading-relaxed"
          style={{ color: '#6B5B7A' }}
        >
          {t(slide.descKey)}
        </p>
      </div>

      {/* 하단: 인디케이터 + 버튼 */}
      <div className="relative z-10 px-8 pb-12">
        <div className="mb-6 flex justify-center gap-2">
          {onboardingSlides.map((_, index) => (
            <div
              key={index}
              className="rounded-full transition-all duration-300"
              style={{
                width: currentSlide === index ? '24px' : '8px',
                height: '8px',
                background:
                  currentSlide === index
                    ? 'linear-gradient(90deg, #B8A4D4 0%, #E8B4A0 100%)'
                    : 'rgba(184, 164, 212, 0.35)',
              }}
            />
          ))}
        </div>

        <button
          onClick={nextSlide}
          className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white shadow-lg transition-all hover:shadow-xl"
          style={{
            background:
              'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 8px 24px rgba(184, 164, 212, 0.35)',
          }}
        >
          {currentSlide === onboardingSlides.length - 1
            ? t('onboarding.start')
            : t('onboarding.next')}
          <ChevronRight className="h-5 w-5" />
        </button>

        {currentSlide === onboardingSlides.length - 1 && (
          <p className="mt-4 text-center text-sm" style={{ color: '#8B7A9E' }}>
            이미 계정이 있으신가요?{' '}
            <button
              type="button"
              onClick={() => onNavigate?.('login')}
              className="font-semibold"
              style={{ color: '#9B7EBD' }}
            >
              로그인
            </button>
          </p>
        )}
      </div>
    </div>
  )
}
