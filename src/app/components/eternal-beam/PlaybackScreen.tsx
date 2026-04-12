import { useState, useEffect } from 'react'
import { Home, Plus, Play } from 'lucide-react'
import { useImage } from '../../contexts/ImageContext'
import { isVideoUrl } from '../../utils/mediaType'

interface PlaybackScreenProps {
  onNavigate?: (screen: string) => void
}

const DEPTH_INTENSITY = 1.5

export function PlaybackScreen({ onNavigate }: PlaybackScreenProps) {
  const { selectedImage } = useImage()
  const [composedPreview, setComposedPreview] = useState<string | null>(null)
  const [depthUrl, setDepthUrl] = useState<string | null>(null)

  useEffect(() => {
    const preview = localStorage.getItem('eternal_beam_composed_preview') || localStorage.getItem('eternal_beam_main_video_url')
    setComposedPreview(preview)
    const depth = localStorage.getItem('eternal_beam_depth_url')
    if (depth) setDepthUrl(depth)
  }, [])

  const displaySrc = composedPreview ?? selectedImage ?? null
  const isVideo = displaySrc ? isVideoUrl(displaySrc) : false

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)',
      }}
    >
      {/* Header */}
      <div className="glass-dark sticky top-0 z-10 left-0 right-0 px-6 py-4 rounded-b-2xl border-0">
        <h1
          className="font-headline text-center text-xl font-bold text-white"
        >
          슬롯 삽입 완료! 🎉
        </h1>
      </div>

      {/* Main Content - Preview/Playback Area */}
      <div className="flex h-full flex-col items-center justify-center px-6 py-20">
        <div
          className="relative mb-8 flex h-56 w-56 items-center justify-center overflow-hidden rounded-3xl"
          style={{
            background: displaySrc
              ? 'transparent'
              : 'linear-gradient(135deg, rgba(102, 126, 234, 0.2) 0%, rgba(118, 75, 162, 0.2) 100%)',
            border: '2px solid rgba(102, 126, 234, 0.3)',
            boxShadow: '0 0 40px rgba(102, 126, 234, 0.2)',
          }}
        >
          {displaySrc ? (
            isVideo ? (
              <video
                src={displaySrc}
                className="h-full w-full object-cover"
                controls
                loop
                muted
                playsInline
                autoPlay
              />
            ) : (
              <div className="relative h-full w-full">
                <img
                  src={displaySrc}
                  alt="합성된 미리보기"
                  className="h-full w-full object-cover"
                  style={{
                    filter: `brightness(${0.7 + DEPTH_INTENSITY * 0.1}) contrast(1.1)`,
                    mixBlendMode: 'screen',
                  }}
                />
                {depthUrl && (
                  <div
                    className="pointer-events-none absolute inset-0"
                    style={{
                      WebkitMaskImage: `url(${depthUrl})`,
                      maskImage: `url(${depthUrl})`,
                      WebkitMaskSize: 'cover',
                      maskSize: 'cover',
                      WebkitMaskRepeat: 'no-repeat',
                      maskRepeat: 'no-repeat',
                      background:
                        'linear-gradient(135deg, rgba(0, 212, 255, 0.9), rgba(118, 75, 162, 0.9))',
                      backgroundSize: '200% 200%',
                      opacity: Math.min(0.2 + DEPTH_INTENSITY * 0.2, 0.85),
                      mixBlendMode: 'screen',
                      animation: 'depthGlow 6s ease-in-out infinite',
                    }}
                  />
                )}
              </div>
            )
          ) : (
            <div className="text-7xl">✨</div>
          )}
          <div
            className="absolute inset-0 flex items-center justify-center rounded-3xl"
            style={{
              background: 'rgba(0, 0, 0, 0.2)',
              opacity: 0.8,
            }}
          >
            <div
              className="flex h-20 w-20 items-center justify-center rounded-full"
              style={{
                background: 'rgba(255, 255, 255, 0.2)',
                backdropFilter: 'blur(10px)',
              }}
            >
              <Play className="h-10 w-10 text-white ml-1" fill="white" />
            </div>
          </div>
        </div>

        <p className="mb-12 text-center text-sm" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
          홀로그램 콘텐츠가 생성되었습니다
        </p>

        {/* Action Buttons */}
        <div className="w-full max-w-xs space-y-3">
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong flex w-full items-center justify-center gap-3 rounded-2xl py-4 font-bold text-white transition-all"
            style={{
              background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
              boxShadow: '0 8px 24px rgba(155, 126, 189, 0.3)',
            }}
          >
            <Home className="h-5 w-5" />
            홈으로
          </button>

          <button
            onClick={() => onNavigate?.('photoUpload')}
            className="glass flex w-full items-center justify-center gap-3 rounded-2xl py-4 font-bold transition-all text-white border-2 border-white/30"
          >
            <Plus className="h-5 w-5" />
            새 추억
          </button>
        </div>
      </div>

      <style>{`
        @keyframes depthGlow {
          0% {
            transform: translate3d(-10%, -10%, 0) scale(1.03);
            background-position: 0% 0%;
          }
          50% {
            transform: translate3d(10%, 10%, 0) scale(1.06);
            background-position: 100% 100%;
          }
          100% {
            transform: translate3d(-10%, -10%, 0) scale(1.03);
            background-position: 0% 0%;
          }
        }
      `}</style>
    </div>
  )
}
