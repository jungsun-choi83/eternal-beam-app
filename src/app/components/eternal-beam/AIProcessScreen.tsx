import { Sparkles, Check } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { useState, useEffect } from 'react'

interface AIProcessScreenProps {
  onNavigate?: (screen: string) => void
}

const steps = [
  { key: 'analyzing', duration: 2000 },
  { key: 'depth', duration: 3000 },
  { key: 'rendering', duration: 3500 },
  { key: 'finalizing', duration: 1500 },
]

export function AIProcessScreen({ onNavigate }: AIProcessScreenProps) {
  const { t } = useLanguage()
  const { selectedImage } = useImage()
  const [progress, setProgress] = useState(0)
  const [currentStep, setCurrentStep] = useState(0)

  useEffect(() => {
    let totalTime = 0
    const totalDuration = steps.reduce((acc, step) => acc + step.duration, 0)

    const interval = setInterval(() => {
      totalTime += 50
      const newProgress = Math.min((totalTime / totalDuration) * 100, 100)
      setProgress(newProgress)

      let accumulated = 0
      for (let i = 0; i < steps.length; i++) {
        accumulated += steps[i].duration
        if (totalTime < accumulated) {
          setCurrentStep(i)
          break
        }
      }

      if (totalTime >= totalDuration) {
        clearInterval(interval)
        setTimeout(() => {
          onNavigate?.('theme')
        }, 500)
      }
    }, 50)

    return () => clearInterval(interval)
  }, [onNavigate])

  return (
    <div
      className="relative flex h-full w-full flex-col items-center justify-center overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)',
      }}
    >
      {/* Animated Background */}
      <div className="absolute inset-0 overflow-hidden">
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 30% 50%, rgba(0, 212, 255, 0.3) 0%, transparent 50%)',
            animation: 'float 6s ease-in-out infinite',
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 70% 50%, rgba(118, 75, 162, 0.3) 0%, transparent 50%)',
            animation: 'float 8s ease-in-out infinite reverse',
          }}
        />
      </div>

      {/* Content */}
      <div className="relative z-10 flex max-w-md flex-col items-center px-8">
        {/* Image Preview */}
        <div className="glass-strong relative mb-8 flex h-32 w-32 items-center justify-center overflow-hidden rounded-full">
          {selectedImage ? (
            <img
              src={selectedImage}
              alt="Processing"
              className="h-full w-full object-cover"
            />
          ) : (
            <Sparkles className="h-16 w-16 text-white" />
          )}
          <div
            className="absolute inset-0 rounded-full border-2 border-white/50"
            style={{
              animation: 'pulse 2s ease-in-out infinite',
            }}
          />
        </div>

        {/* Title */}
        <h1
          className="font-headline mb-4 text-center text-3xl font-bold text-white"
          style={{
          }}
        >
          {t('aiprocess.title')}
        </h1>

        {/* Progress Bar */}
        <div
          className="mb-8 h-2 w-full overflow-hidden rounded-full"
          style={{
            background: 'rgba(255, 255, 255, 0.2)',
          }}
        >
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: `${progress}%`,
              background: 'linear-gradient(90deg, #00d4ff 0%, #ffffff 100%)',
              boxShadow: '0 0 20px rgba(0, 212, 255, 0.6)',
            }}
          />
        </div>

        {/* Steps */}
        <div className="w-full space-y-4">
          {steps.map((step, index) => (
            <div
              key={step.key}
              className="flex items-center gap-4 rounded-2xl p-4 transition-all"
              style={{
                background:
                  index <= currentStep
                    ? 'rgba(255, 255, 255, 0.2)'
                    : 'rgba(255, 255, 255, 0.05)',
                backdropFilter: 'blur(10px)',
                border:
                  index === currentStep
                    ? '2px solid rgba(255, 255, 255, 0.5)'
                    : '2px solid transparent',
              }}
            >
              <div
                className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-full"
                style={{
                  background:
                    index < currentStep
                      ? 'linear-gradient(135deg, #00d4ff 0%, #ffffff 100%)'
                      : index === currentStep
                        ? 'rgba(255, 255, 255, 0.3)'
                        : 'rgba(255, 255, 255, 0.1)',
                }}
              >
                {index < currentStep ? (
                  <Check className="h-5 w-5 text-white" />
                ) : (
                  <span className="font-bold text-white">{index + 1}</span>
                )}
              </div>
              <div className="flex-1">
                <p className="font-semibold text-white">
                  {t(`aiprocess.${step.key}`)}
                </p>
              </div>
              {index === currentStep && (
                <div className="flex gap-1">
                  <div
                    className="h-2 w-2 animate-bounce rounded-full bg-white"
                    style={{ animationDelay: '0ms' }}
                  />
                  <div
                    className="h-2 w-2 animate-bounce rounded-full bg-white"
                    style={{ animationDelay: '150ms' }}
                  />
                  <div
                    className="h-2 w-2 animate-bounce rounded-full bg-white"
                    style={{ animationDelay: '300ms' }}
                  />
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Percentage */}
        <div className="mt-8 text-center">
          <div
            className="mb-2 text-6xl font-bold text-white"
            style={{
              fontFamily: 'var(--font-headline)',
            }}
          >
            {Math.round(progress)}%
          </div>
          <p className="text-sm text-white/80">잠시만 기다려주세요...</p>
        </div>
      </div>

      <style>{`
        @keyframes float {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(20px, -20px) scale(1.1); }
        }
        @keyframes pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.15); opacity: 0.5; }
        }
      `}</style>
    </div>
  )
}
