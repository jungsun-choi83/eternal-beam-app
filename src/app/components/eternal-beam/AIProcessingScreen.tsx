import { Sparkles, Check } from 'lucide-react'
import { useImage } from '../../contexts/ImageContext'
import { useState, useEffect } from 'react'

interface AIProcessingScreenProps {
  onNavigate?: (screen: string) => void
}

const steps = [
  { key: '사진 분석 중...', duration: 1000 },
  { key: '깊이 맵 생성 중...', duration: 2000 },
  { key: '3D 홀로그램 렌더링 중...', duration: 3000 },
  { key: '최종 처리 중...', duration: 1000 },
]

export function AIProcessingScreen({ onNavigate }: AIProcessingScreenProps) {
  const { selectedImage } = useImage()
  const [progress, setProgress] = useState(0)
  const [currentStep, setCurrentStep] = useState(0)

  useEffect(() => {
    const totalDuration = steps.reduce((acc, s) => acc + s.duration, 0)
    let totalTime = 0

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
        const videoId =
          'holo_' +
          Date.now() +
          '_' +
          Math.random().toString(36).substring(2, 9)
        localStorage.setItem('eternal_beam_hologram_video_id', videoId)
        localStorage.setItem('eternal_beam_current_video_id', videoId)
        localStorage.setItem('eternal_beam_hologram_status', 'completed')
        setTimeout(() => onNavigate?.('record'), 500)
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
        </div>

        <h1
          className="font-headline mb-6 text-center text-2xl font-bold text-white"
        >
          3D 홀로그램 생성 중
        </h1>

        {/* Progress Bar */}
        <div className="glass mb-6 h-3 w-full overflow-hidden rounded-full">
          <div
            className="h-full rounded-full transition-all duration-300"
            style={{
              width: `${progress}%`,
              background:
                'linear-gradient(90deg, #00d4ff 0%, #ffffff 100%)',
              boxShadow: '0 0 20px rgba(0, 212, 255, 0.6)',
            }}
          />
        </div>
        <p className="mb-8 text-2xl font-bold text-white">
          {Math.round(progress)}%
        </p>

        {/* Steps */}
        <div className="w-full space-y-3">
          {steps.map((step, index) => (
            <div
              key={step.key}
              className="flex items-center gap-4 rounded-xl px-4 py-3"
              style={{
                background:
                  index <= currentStep
                    ? 'rgba(255, 255, 255, 0.2)'
                    : 'rgba(255, 255, 255, 0.05)',
              }}
            >
              <div
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full"
                style={{
                  background:
                    index < currentStep
                      ? 'linear-gradient(135deg, #00d4ff, #ffffff)'
                      : index === currentStep
                        ? 'rgba(255, 255, 255, 0.4)'
                        : 'rgba(255, 255, 255, 0.1)',
                }}
              >
                {index < currentStep ? (
                  <Check className="h-4 w-4 text-white" />
                ) : (
                  <span className="text-sm font-bold text-white">
                    {index + 1}
                  </span>
                )}
              </div>
              <p className="text-sm font-semibold text-white">{step.key}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
