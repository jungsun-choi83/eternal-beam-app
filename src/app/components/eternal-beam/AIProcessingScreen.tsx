// src/app/components/eternal-beam/AIProcessingScreen.tsx
import { Sparkles, Check } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { useState, useEffect } from 'react'
import { createZoeDepthPreview } from '../../services/zoeHologramService'
import { cutoutImage } from '../../services/videoProcessingApi'

interface AIProcessingScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

const steps = [
  { key: 'analyzing', duration: 2000 },   // Step 1: 누끼
  { key: 'runway', duration: 4000 },       // Step 2: Runway 모션
  { key: 'finalizing', duration: 2000 },  // Step 3: 최적화
]

export function AIProcessingScreen({ onNavigate, onBack }: AIProcessingScreenProps) {
  const { t } = useLanguage()
  const { selectedImage } = useImage()
  const [progress, setProgress] = useState(0)
  const [currentStep, setCurrentStep] = useState(0)
  const [cutoutDone, setCutoutDone] = useState(false)
  const [cutoutLoading, setCutoutLoading] = useState(false)
  const apiUrl = (import.meta as any).env?.VITE_VIDEO_API_URL || (import.meta as any).env?.VITE_API_URL
  const useCutoutApi = !!apiUrl
  // 데모 모드: 로컬 노트북에서는 누끼 서버 대신 원본 이미지를 바로 사용 (로딩을 짧게)
  const isDemoMode = true

  // ZoeDepth 테스트용 상태
  const [depthUrl, setDepthUrl] = useState<string | null>(null)
  const [isZoeLoading, setIsZoeLoading] = useState(false)
  const [zoeError, setZoeError] = useState<string | null>(null)

  useEffect(() => {
    // 데모 모드에서는 누끼 서버 호출을 생략하고, 바로 원본 이미지를 cutout으로 사용
    if (isDemoMode || !useCutoutApi) {
      const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
      if (mainPhoto) {
        localStorage.setItem('eternal_beam_cutout_url', mainPhoto)
      }
      setCutoutDone(true)
      return
    }
    let cancelled = false
    setCutoutLoading(true)
    const run = async () => {
      const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
      if (!mainPhoto || mainPhoto.startsWith('blob:')) {
        if (!cancelled) {
          setCutoutLoading(false)
          setCutoutDone(true)
        }
        return
      }
      try {
        const res = await fetch(mainPhoto)
        const blob = await res.blob()
        const file = new File([blob], 'photo.jpg', { type: blob.type || 'image/jpeg' })
        const contentId = localStorage.getItem('eternal_beam_current_content_id') || `content_${Date.now()}`
        // 로컬 첫 실행 시 rembg 모델 다운로드 때문에 시간이 많이 걸릴 수 있으므로 여유 있게 2분까지 기다림
        const timeoutMs = 120000
        const result = await Promise.race([
          cutoutImage(file, { contentId, saveToStorage: true }),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error('서버 응답 시간이 초과되었습니다.')), timeoutMs),
          ),
        ])
        if (cancelled) return
        if (result.cutout_url) {
          localStorage.setItem('eternal_beam_cutout_url', result.cutout_url)
        } else if (result.cutout_png_base64) {
          localStorage.setItem('eternal_beam_cutout_base64', result.cutout_png_base64)
        }
      } catch (e) {
        if (!cancelled) {
          console.warn('서버 누끼 API 실패, 데모 모드로 원본 이미지를 사용합니다:', e)
          // 데모 모드 fallback: 누끼 실패 시에도 전체 플로우가 이어지도록 원본 이미지를 cutout처럼 사용
          const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
          if (mainPhoto) {
            localStorage.setItem('eternal_beam_cutout_url', mainPhoto)
          }
        }
      } finally {
        if (!cancelled) {
          setCutoutLoading(false)
          setCutoutDone(true)
        }
      }
    }
    void run()
    return () => { cancelled = true }
  }, [useCutoutApi])

  useEffect(() => {
    if (!cutoutDone) return
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
          onNavigate?.('themeSelection')
        }, 500)
      }
    }, 50)

    return () => clearInterval(interval)
  }, [cutoutDone, onNavigate])

  // ZoeDepth 자동 생성 - 미리보기 깊이감 (기존)
  useEffect(() => {
    let cancelled = false
    const run = async () => {
      try {
        const existing = localStorage.getItem('eternal_beam_depth_url')
        if (existing) return
        const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
        if (!mainPhoto) return
        const res = await fetch(mainPhoto)
        const blob = await res.blob()
        const url = await createZoeDepthPreview(blob)
        if (cancelled) return
        localStorage.setItem('eternal_beam_depth_url', url)
        setDepthUrl(url)
      } catch (error) {
        if (!cancelled) console.error('ZoeDepth 자동 생성 중 오류:', error)
      }
    }
    void run()
    return () => { cancelled = true }
  }, [])

  // ZoeDepth API 테스트 호출
  const handleZoeTest = async () => {
    try {
      setIsZoeLoading(true)
      setZoeError(null)
      setDepthUrl(null)

      const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
      if (!mainPhoto) {
        setZoeError('먼저 사진을 업로드해주세요.')
        return
      }

      const res = await fetch(mainPhoto)
      const blob = await res.blob()

      const url = await createZoeDepthPreview(blob)
      setDepthUrl(url)
    } catch (err) {
      setZoeError(
        err instanceof Error ? err.message : 'ZoeDepth 호출 중 오류가 발생했습니다.',
      )
    } finally {
      setIsZoeLoading(false)
    }
  }

  return (
    <div
      className="relative flex h-full w-full flex-col items-center justify-center overflow-hidden"
      style={{ background: '#000000' }}
    >
      {/* Animated Background - glow only */}
      <div className="absolute inset-0 overflow-hidden">
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 30% 50%, rgba(0, 212, 255, 0.15) 0%, transparent 50%)',
            animation: 'float 6s ease-in-out infinite',
          }}
        />
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at 70% 50%, rgba(118, 75, 162, 0.15) 0%, transparent 50%)',
            animation: 'float 8s ease-in-out infinite reverse',
          }}
        />
      </div>

      {/* 감성 로딩: Cutout API 대기 중 */}
      {cutoutLoading && (
        <div className="relative z-20 flex flex-col items-center justify-center px-8">
          {onBack && (
            <button
              onClick={onBack}
              className="absolute left-4 top-4 rounded-full p-2 text-white/70 hover:bg-white/10 hover:text-white"
              aria-label="뒤로"
            >
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            </button>
          )}
          <div
            className="mb-8 flex h-28 w-28 items-center justify-center rounded-full border-2 border-cyan-400/40"
            style={{
              animation: 'heartbeat 1.5s ease-in-out infinite',
              boxShadow: '0 0 50px rgba(0,212,255,0.3)',
            }}
          >
            <span className="text-6xl" style={{ animation: 'pulse 2s ease-in-out infinite' }}>✨</span>
          </div>
          <h1 className="mb-3 text-center text-xl font-bold text-white">
            {t('aiprocess.title')}
          </h1>
          <p className="mb-2 text-center text-white/90">
            아이와 재회를 준비 중입니다...
          </p>
          <p className="text-center text-sm text-white/60">
            잠시만 기다려 주세요
          </p>
          <div className="mt-8 flex gap-2">
            <div className="h-2 w-2 animate-bounce rounded-full bg-cyan-400" style={{ animationDelay: '0ms' }} />
            <div className="h-2 w-2 animate-bounce rounded-full bg-cyan-400" style={{ animationDelay: '200ms' }} />
            <div className="h-2 w-2 animate-bounce rounded-full bg-cyan-400" style={{ animationDelay: '400ms' }} />
          </div>
        </div>
      )}

      {/* Content (cutout 완료 후 또는 API 미사용 시) */}
      {!cutoutLoading && (
      <div className="relative z-10 flex max-w-md flex-col items-center px-8">
        {/* Image Preview */}
        <div
          className="relative mb-8 flex h-32 w-32 items-center justify-center overflow-hidden rounded-full border border-white/10"
          style={{ boxShadow: '0 0 40px rgba(0,212,255,0.2)' }}
        >
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
        <h1 className="font-headline mb-2 text-center text-2xl font-bold text-white">
          {t('aiprocess.title')}
        </h1>
        <p className="mb-6 text-center text-sm text-white/70">
          아이와 재회를 준비 중입니다...
        </p>

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
            className="mb-2 text-5xl font-bold text-white"
            style={{ fontFamily: 'var(--font-headline)' }}
          >
            {Math.round(progress)}%
          </div>
          <p className="text-sm text-white/70">잠시만 기다려주세요...</p>
        </div>

        {/* ZoeDepth 테스트 영역 (개발 모드에서만) */}
        {(import.meta as any).env?.DEV && (
          <div className="mt-10 w-full space-y-3 rounded-2xl bg-black/20 p-4">
            <h3 className="text-sm font-semibold text-white">
              ZoeDepth 3D 테스트 (개발용)
            </h3>
            <button
              type="button"
              onClick={handleZoeTest}
              disabled={isZoeLoading}
              className="w-full rounded-xl bg-white/10 px-4 py-3 text-sm font-medium text-white hover:bg-white/20 transition disabled:opacity-50"
            >
              {isZoeLoading ? 'AI 3D 변환 중...' : '현재 사진으로 ZoeDepth 테스트하기'}
            </button>

            {zoeError && (
              <p className="text-xs text-red-300 mt-1">{zoeError}</p>
            )}

            {depthUrl && (
              <div className="mt-2">
                <p className="mb-1 text-xs text-white/80">생성된 깊이맵 미리보기</p>
                <div className="overflow-hidden rounded-xl border border-white/20 bg-black/40">
                  <img
                    src={depthUrl}
                    alt="ZoeDepth 미리보기"
                    className="w-full h-40 object-contain"
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      )}

      <style>{`
        @keyframes float {
          0%, 100% { transform: translate(0, 0) scale(1); }
          50% { transform: translate(20px, -20px) scale(1.1); }
        }
        @keyframes heartbeat {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.05); opacity: 0.9; }
        }
        @keyframes pulse {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.15); opacity: 0.5; }
        }
      `}</style>
    </div>
  )
}