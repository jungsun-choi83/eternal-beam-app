import { ArrowRight, Download, Share2, Zap, RotateCcw, Image as ImageIcon, Palette } from 'lucide-react'
import { useImage } from '../../contexts/ImageContext'
import { useState, useEffect } from 'react'
import { isVideoUrl } from '../../utils/mediaType'

interface PreviewScreenProps {
  onNavigate?: (screen: string) => void
}

export function PreviewScreen({ onNavigate }: PreviewScreenProps) {
  const { selectedImage, setSelectedImage, setMediaType } = useImage()
  const [previewSrc, setPreviewSrc] = useState<string | null>(selectedImage)
  const [isVideo, setIsVideo] = useState(false)

  useEffect(() => {
    const synthesizedUrl = localStorage.getItem('eternal_beam_synthesized_mp4_url')
    const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
    const mainVideo = localStorage.getItem('eternal_beam_main_video_url')
    const composedPreview = localStorage.getItem('eternal_beam_composed_preview')
    const mediaType = localStorage.getItem('eternal_beam_media_type') as 'image' | 'video' | null
    const src =
      synthesizedUrl ||
      composedPreview ||
      mainPhoto ||
      mainVideo ||
      selectedImage
    setPreviewSrc(src)
    const video =
      !!synthesizedUrl ||
      mediaType === 'video' ||
      (src && isVideoUrl(src))
    setIsVideo(!!video)
    if (src && !selectedImage) {
      setSelectedImage(src)
      if (mediaType && !synthesizedUrl) setMediaType(mediaType)
    }
  }, [selectedImage, setSelectedImage, setMediaType])

  const displaySrc = previewSrc || selectedImage
  const [depthIntensity, setDepthIntensity] = useState(1.5)
  const [rotation, setRotation] = useState(0)

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)',
      }}
    >
      {/* Header */}
      <div className="glass-dark sticky top-0 z-10 left-0 right-0 px-6 py-4 rounded-b-2xl border-0">
        <div className="flex items-center justify-between">
          <h1
            className="text-lg font-bold text-white"
            style={{
              fontFamily: 'var(--font-headline)',
            }}
          >
            홀로그램 미리보기
          </h1>

          <div className="flex items-center gap-2">
            <button
              onClick={() => {
                localStorage.setItem('eternal_beam_edit_photo_only', '1')
                onNavigate?.('photoUpload')
              }}
              type="button"
              className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
              title="사진/동영상만 변경"
            >
              <ImageIcon className="h-5 w-5 text-white" />
            </button>
            <button
              onClick={() => {
                localStorage.setItem('eternal_beam_edit_theme_only', '1')
                onNavigate?.('themeSelection')
              }}
              type="button"
              className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
              title="테마/배경만 변경"
            >
              <Palette className="h-5 w-5 text-white" />
            </button>
            <button
              type="button"
              className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
            >
              <Share2 className="h-5 w-5 text-white" />
            </button>
            <button
              onClick={() => {
                const target = localStorage.getItem('eternal_beam_storage_target')
                onNavigate?.(target === 'slot' ? 'checkout' : 'qrcode')
              }}
              type="button"
              className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
            >
              <ArrowRight className="h-5 w-5 text-white" />
            </button>
          </div>
        </div>
      </div>

      {/* 2D Preview Area - Three.js 없이 이미지 미리보기 */}
      <div className="absolute inset-0 flex items-center justify-center pt-16 pb-64">
          <div
            className="glass-dark relative flex h-64 w-64 items-center justify-center overflow-hidden rounded-2xl border-2 border-white/20"
            style={{
              boxShadow: '0 0 60px rgba(155, 126, 189, 0.3)',
            transform: `rotateY(${rotation}deg) perspective(600px)`,
            transformStyle: 'preserve-3d',
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
              <img
                src={displaySrc}
                alt="홀로그램 미리보기"
                className="h-full w-full object-cover"
                style={{
                  filter: `brightness(${0.7 + depthIntensity * 0.1}) contrast(1.1)`,
                  mixBlendMode: 'screen',
                }}
              />
            )
          ) : (
            <div
              className="flex h-full w-full items-center justify-center text-6xl"
              style={{ color: 'rgba(255, 255, 255, 0.5)' }}
            >
              ✨
            </div>
          )}
          {/* Hologram scan line effect */}
          <div
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0, 212, 255, 0.05) 2px, rgba(0, 212, 255, 0.05) 4px)',
              animation: 'scanLine 3s linear infinite',
            }}
          />
        </div>
      </div>

      {/* Overlay */}
      <div className="pointer-events-none absolute inset-0">
        <div
          className="absolute inset-0"
          style={{
            background:
              'radial-gradient(circle at center, transparent 0%, rgba(0, 0, 0, 0.5) 100%)',
          }}
        />
      </div>

      {/* Control Panel */}
        <div className="glass-dark absolute bottom-0 left-0 right-0 z-10 px-6 py-6 rounded-t-2xl">
        <div className="mb-6">
          <div className="mb-3 flex items-center justify-between">
            <label className="flex items-center gap-2 text-sm font-semibold text-white">
              <Zap className="h-4 w-4 text-[#00d4ff]" />
              깊이 강도
            </label>
            <span className="text-sm font-bold text-[#00d4ff]">
              {Math.round(depthIntensity * 100)}%
            </span>
          </div>
          <input
            type="range"
            min="0"
            max="3"
            step="0.1"
            value={depthIntensity}
            onChange={(e) => setDepthIntensity(parseFloat(e.target.value))}
            className="h-2 w-full cursor-pointer appearance-none rounded-full"
            style={{
              background: `linear-gradient(to right, #667eea 0%, #00d4ff ${(depthIntensity / 3) * 100}%, rgba(255,255,255,0.2) ${(depthIntensity / 3) * 100}%)`,
            }}
          />
        </div>

        <div className="mb-4 grid grid-cols-2 gap-3">
          <button
            onClick={() => {
              localStorage.setItem('eternal_beam_edit_photo_only', '1')
              onNavigate?.('photoUpload')
            }}
            type="button"
            className="glass flex items-center justify-center gap-2 rounded-xl px-4 py-3 border border-white/20 text-white"
          >
            <ImageIcon className="h-5 w-5" />
            <span className="text-sm font-semibold">사진/동영상 변경</span>
          </button>
          <button
            onClick={() => {
              localStorage.setItem('eternal_beam_edit_theme_only', '1')
              onNavigate?.('themeSelection')
            }}
            type="button"
            className="glass flex items-center justify-center gap-2 rounded-xl px-4 py-3 border border-white/20 text-white"
          >
            <Palette className="h-5 w-5" />
            <span className="text-sm font-semibold">테마/배경 변경</span>
          </button>
          <button
            onClick={() => setRotation((r) => (r + 45) % 360)}
            type="button"
            className="glass-strong flex items-center justify-center gap-2 rounded-xl px-4 py-3 border border-white/20 text-white"
            style={{
              background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            }}
          >
            <RotateCcw className="h-5 w-5" />
            <span className="text-sm font-semibold">회전</span>
          </button>

          <button
            type="button"
            className="glass flex items-center justify-center gap-2 rounded-xl px-4 py-3 border border-white/20 text-white"
          >
            <Download className="h-5 w-5" />
            <span className="text-sm font-semibold">다운로드</span>
          </button>
        </div>

        <button
          onClick={() => {
            const target = localStorage.getItem('eternal_beam_storage_target') ?? 'hardware'
            onNavigate?.(target === 'slot' ? 'checkout' : 'qrcode')
          }}
          type="button"
          className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all"
          style={{
            background:
              'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 8px 32px rgba(155, 126, 189, 0.4)',
          }}
        >
          <ArrowRight className="h-5 w-5" />
          {(localStorage.getItem('eternal_beam_storage_target') ?? 'hardware') === 'slot'
            ? '결제하기'
            : 'QR 연결'}
        </button>
      </div>

      <style>{`
        @keyframes scanLine {
          0% { transform: translateY(-100%); }
          100% { transform: translateY(100%); }
        }
        input[type="range"]::-webkit-slider-thumb {
          appearance: none;
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: linear-gradient(135deg, #00d4ff 0%, #ffffff 100%);
          cursor: pointer;
          box-shadow: 0 0 10px rgba(0, 212, 255, 0.8);
        }
        input[type="range"]::-moz-range-thumb {
          width: 20px;
          height: 20px;
          border-radius: 50%;
          background: linear-gradient(135deg, #00d4ff 0%, #ffffff 100%);
          cursor: pointer;
          border: none;
          box-shadow: 0 0 10px rgba(0, 212, 255, 0.8);
        }
      `}</style>
    </div>
  )
}
