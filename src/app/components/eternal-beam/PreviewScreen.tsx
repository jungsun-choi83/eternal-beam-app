import { ArrowRight, ArrowLeft, Image as ImageIcon, Palette } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useImage } from '../../contexts/ImageContext'
import { isVideoUrl } from '../../utils/mediaType'

interface PreviewScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

export function PreviewScreen({ onNavigate, onBack }: PreviewScreenProps) {
  const { previewControls, setPreviewControls, resetPreviewControls, setContentId, setSelectedImage, setMediaType } =
    useImage()

  const [src, setSrc] = useState<string | null>(null)
  const [isVideo, setIsVideo] = useState(false)

  useEffect(() => {
    const synthesizedUrl = localStorage.getItem('eternal_beam_synthesized_mp4_url')
    const composedPreview = localStorage.getItem('eternal_beam_composed_preview')
    const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
    const mainVideo = localStorage.getItem('eternal_beam_main_video_url')
    const mediaType = localStorage.getItem('eternal_beam_media_type') as 'image' | 'video' | null

    const chosen = synthesizedUrl || composedPreview || mainPhoto || mainVideo
    if (chosen) {
      setSrc(chosen)
      setSelectedImage(chosen)
    }
    const video = !!synthesizedUrl || mediaType === 'video' || (chosen && isVideoUrl(chosen))
    setIsVideo(!!video)
    if (mediaType && !synthesizedUrl) {
      setMediaType(mediaType)
    }
  }, [setSelectedImage, setMediaType])

  const handleComplete = () => {
    const demoId = `demo-content-${Date.now()}`
    const payload = { content_id: demoId, slot_number: 1 }
    localStorage.setItem('eternal_beam_content_id', demoId)
    localStorage.setItem('eternal_beam_nfc_payload', JSON.stringify(payload))
    setContentId(demoId)
    onNavigate?.('nfcPlayback')
  }

  const { scale, positionX, positionY } = previewControls

  return (
    <div
      className="relative h-full w-full overflow-y-auto"
      style={{ background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)' }}
    >
      {/* Header */}
      <div className="glass-dark sticky top-0 z-10 left-0 right-0 px-6 py-4 rounded-b-2xl border-0">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-bold text-white" style={{ fontFamily: 'var(--font-headline)' }}>
            홀로그램 미리보기
          </h1>
          <div className="flex items-center gap-2">
            {onBack && (
              <button
                onClick={onBack}
                type="button"
                className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
                title="뒤로"
              >
                <ArrowLeft className="h-5 w-5 text-white" />
              </button>
            )}
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
          </div>
        </div>
      </div>

      <div className="flex flex-col items-center gap-6 px-6 py-6 pb-24">
        {/* Preview box */}
        <div className="glass-dark relative flex h-64 w-64 items-center justify-center rounded-2xl border-2 border-white/20 overflow-hidden">
          {src ? (
            isVideo ? (
              <video
                src={src}
                className="h-full w-full object-cover"
                loop
                muted
                playsInline
                autoPlay
                style={{
                  transform: `translate(${positionX}%, ${positionY}%) scale(${scale})`,
                  transformOrigin: 'center center',
                }}
              />
            ) : (
              <img
                src={src}
                alt="홀로그램 미리보기"
                className="h-full w-full object-cover"
                style={{
                  transform: `translate(${positionX}%, ${positionY}%) scale(${scale})`,
                  transformOrigin: 'center center',
                }}
              />
            )
          ) : (
            <div className="text-white/60 text-sm text-center px-4">
              이전 단계에서 선택한 사진이나 영상이 여기 미리보기로 표시됩니다.
            </div>
          )}
        </div>

        {/* Controls */}
        <div className="glass-dark w-full max-w-md px-6 py-6 rounded-2xl border border-white/10 space-y-4">
          <div>
            <label className="flex items-center gap-2 text-sm font-semibold text-white mb-1">
              크기: {scale.toFixed(1)}x
            </label>
            <input
              type="range"
              min="0.5"
              max="2"
              step="0.1"
              value={scale}
              onChange={(e) =>
                setPreviewControls({ ...previewControls, scale: parseFloat((e.target as HTMLInputElement).value) })
              }
              className="h-2 w-full cursor-pointer appearance-none rounded-full"
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-white mb-1">위치 (좌우): {positionX}%</label>
            <input
              type="range"
              min="-100"
              max="100"
              step="5"
              value={positionX}
              onChange={(e) =>
                setPreviewControls({
                  ...previewControls,
                  positionX: parseInt((e.target as HTMLInputElement).value, 10) || 0,
                })
              }
              className="h-2 w-full cursor-pointer appearance-none rounded-full"
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-white mb-1">위치 (상하): {positionY}%</label>
            <input
              type="range"
              min="-100"
              max="100"
              step="5"
              value={positionY}
              onChange={(e) =>
                setPreviewControls({
                  ...previewControls,
                  positionY: parseInt((e.target as HTMLInputElement).value, 10) || 0,
                })
              }
              className="h-2 w-full cursor-pointer appearance-none rounded-full"
            />
          </div>
          <button
            type="button"
            onClick={resetPreviewControls}
            className="text-xs text-white/70 hover:text-white underline"
          >
            리셋
          </button>

          <button
            onClick={handleComplete}
            type="button"
            className="mt-4 glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white"
          >
            <ArrowRight className="h-5 w-5" />
            완료 (Content_ID 생성 후 NFC 저장)
          </button>
        </div>
      </div>
    </div>
  )
}
import { ArrowRight, ArrowLeft, Share2, Zap, RotateCcw, Image as ImageIcon, Palette, Maximize2 } from 'lucide-react'
import { useImage } from '../../contexts/ImageContext'
import { useSubjectSlot } from '../../contexts/SubjectSlotContext'
import { useState, useEffect, useCallback } from 'react'
import { isVideoUrl } from '../../utils/mediaType'
import { generatePreview, composeFinal, getVideoApiBaseUrl } from '../../services/videoProcessingApi'

interface PreviewScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

export function PreviewScreen({ onNavigate, onBack }: PreviewScreenProps) {
  const {
    selectedImage,
    setSelectedImage,
    setMediaType,
    contentId,
    setContentId,
    previewControls,
    setPreviewControls,
    resetPreviewControls,
  } = useImage()
  const { selectedThemeId } = useSubjectSlot()
  const isDemoMode = true
  const [previewSrc, setPreviewSrc] = useState<string | null>(selectedImage)
  const [isVideo, setIsVideo] = useState(false)
  const [depthUrl, setDepthUrl] = useState<string | null>(null)
  const [previewVideoUrl, setPreviewVideoUrl] = useState<string | null>(null)
  const [isPreviewLoading, setIsPreviewLoading] = useState(false)

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

    const depth = localStorage.getItem('eternal_beam_depth_url')
    if (depth) {
      setDepthUrl(depth)
    }
    if (src && !selectedImage) {
      setSelectedImage(src)
      if (mediaType && !synthesizedUrl) setMediaType(mediaType)
    }
  }, [selectedImage, setSelectedImage, setMediaType])

  const displaySrc = previewSrc || selectedImage
  const [depthIntensity, setDepthIntensity] = useState(1.5)
  const [rotation, setRotation] = useState(0)

  // Cutout 파일 획득 (localStorage)
  const getCutoutFile = useCallback(async (): Promise<File | null> => {
    const cutoutUrl = localStorage.getItem('eternal_beam_cutout_url')
    const cutoutBase64 = localStorage.getItem('eternal_beam_cutout_base64')
    if (cutoutUrl) {
      const res = await fetch(cutoutUrl)
      const blob = await res.blob()
      return new File([blob], 'cutout.png', { type: 'image/png' })
    }
    if (cutoutBase64) {
      const arr = Uint8Array.from(atob(cutoutBase64), (c) => c.charCodeAt(0))
      const blob = new Blob([arr], { type: 'image/png' })
      return new File([blob], 'cutout.png', { type: 'image/png' })
    }
    return null
  }, [])

  // 실시간 프리뷰 업데이트 (디바운스)
  useEffect(() => {
    if (isDemoMode) {
      return
    }
    const themeId = selectedThemeId || localStorage.getItem('eternal_beam_background_theme_id') || 'christmas'
    const timer = setTimeout(async () => {
      const cutout = await getCutoutFile()
      if (!cutout || !themeId) return
      setIsPreviewLoading(true)
      try {
        const { preview_url } = await generatePreview({
          background_id: themeId,
          cutoutFile: cutout,
          scale: previewControls.scale,
          position_x: previewControls.positionX,
          position_y: previewControls.positionY,
        })
        const fullUrl = preview_url.startsWith('http') ? preview_url : `${getVideoApiBaseUrl()}${preview_url}`
        setPreviewVideoUrl(fullUrl)
      } catch (e) {
        console.warn('프리뷰 생성 실패:', e)
        setPreviewVideoUrl(null)
      } finally {
        setIsPreviewLoading(false)
      }
    }, 800)
    return () => clearTimeout(timer)
  }, [previewControls, selectedThemeId, getCutoutFile])

  const handleFinalCompose = useCallback(async () => {
    if (isDemoMode) {
      const demoId = `demo-content-${Date.now()}`
      const demoPayload = { content_id: demoId, slot_number: 1 }
      localStorage.setItem('eternal_beam_content_id', demoId)
      localStorage.setItem('eternal_beam_nfc_payload', JSON.stringify(demoPayload))
      setContentId(demoId)
      onNavigate?.('nfcPlayback')
      return
    }
    const themeId = selectedThemeId || localStorage.getItem('eternal_beam_background_theme_id') || 'christmas'
    const subjectId = localStorage.getItem('eternal_beam_current_content_id') || `subject_${Date.now()}`
    const userId = 'anonymous'
    try {
      const { content_id, nfc_payload } = await composeFinal({
        background_id: themeId,
        subject_id: subjectId,
        scale: previewControls.scale,
        position_x: previewControls.positionX,
        position_y: previewControls.positionY,
        user_id: userId,
      })
      localStorage.setItem('eternal_beam_content_id', content_id)
      localStorage.setItem('eternal_beam_nfc_payload', JSON.stringify(nfc_payload))
      setContentId(content_id)
    } catch (e) {
      console.error('최종 합성 실패:', e)
    }
    onNavigate?.('nfcPlayback')
  }, [previewControls, selectedThemeId, setContentId, onNavigate])

  return (
    <div
      className="relative h-full w-full"
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
            {onBack && (
              <button
                onClick={onBack}
                type="button"
                className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
                title="뒤로"
              >
                <ArrowLeft className="h-5 w-5 text-white" />
              </button>
            )}
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

      {/* Body: 프리뷰 + 컨트롤 (세로 스크롤 가능) */}
      <div className="flex flex-col items-center gap-6 px-6 py-6 pb-24 overflow-y-auto">
        {/* 2D Preview Area — 레이어 분리 프리뷰 또는 기본 이미지 */}
        <div
          className="glass-dark relative flex h-64 w-64 items-center justify-center overflow-hidden rounded-2xl border-2 border-white/20"
          style={{
            boxShadow: '0 0 60px rgba(155, 126, 189, 0.3)',
            transform: `rotateY(${rotation}deg) perspective(600px)`,
            transformStyle: 'preserve-3d',
          }}
        >
          {previewVideoUrl ? (
            <>
              {isPreviewLoading && (
                <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-black/60 text-white text-sm">
                  처리 중...
                </div>
              )}
              <video
                src={previewVideoUrl}
                className="h-full w-full object-cover"
                loop
                muted
                autoPlay
                playsInline
                key={previewVideoUrl}
              />
            </>
          ) : displaySrc ? (
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
                  alt="홀로그램 미리보기"
                  className="h-full w-full object-cover"
                  style={{
                    filter: `brightness(${0.7 + depthIntensity * 0.1}) contrast(1.1)`,
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
                      opacity: Math.min(0.2 + depthIntensity * 0.2, 0.85),
                      mixBlendMode: 'screen',
                      animation: 'depthGlow 6s ease-in-out infinite',
                    }}
                  />
                )}
              </div>
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

        {/* Control Panel */}
        <div className="glass-dark w-full max-w-md px-6 py-6 rounded-2xl border border-white/10">
        {/* Scale / Position 조정 (Content_ID 레이어 시스템) */}
        <div className="mb-4 space-y-3">
          <div>
            <label className="flex items-center gap-2 text-sm font-semibold text-white mb-1">
              <Maximize2 className="h-4 w-4" /> 크기: {previewControls.scale.toFixed(1)}x
            </label>
            <input
              type="range"
              min="0.5"
              max="2"
              step="0.1"
              value={previewControls.scale}
              onChange={(e) => setPreviewControls({ ...previewControls, scale: parseFloat(e.target.value) })}
              className="h-2 w-full cursor-pointer appearance-none rounded-full"
              style={{
                background: `linear-gradient(to right, #667eea 0%, #00d4ff ${((previewControls.scale - 0.5) / 1.5) * 100}%, rgba(255,255,255,0.2) ${((previewControls.scale - 0.5) / 1.5) * 100}%)`,
              }}
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-white mb-1">위치 (좌우): {previewControls.positionX}%</label>
            <input
              type="range"
              min="-100"
              max="100"
              step="5"
              value={previewControls.positionX}
              onChange={(e) => setPreviewControls({ ...previewControls, positionX: parseInt(e.target.value, 10) })}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-white/20"
            />
          </div>
          <div>
            <label className="text-sm font-semibold text-white mb-1">위치 (상하): {previewControls.positionY}%</label>
            <input
              type="range"
              min="-100"
              max="100"
              step="5"
              value={previewControls.positionY}
              onChange={(e) => setPreviewControls({ ...previewControls, positionY: parseInt(e.target.value, 10) })}
              className="h-2 w-full cursor-pointer appearance-none rounded-full bg-white/20"
            />
          </div>
          <button
            type="button"
            onClick={resetPreviewControls}
            className="text-xs text-white/70 hover:text-white underline"
          >
            리셋
          </button>
        </div>

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
            className="glass flex items-center justify-center gap-2 rounded-xl px-4 py-3 border border-white/20 text-white opacity-50 cursor-not-allowed"
            title="준비 중"
          >
            <span className="text-sm font-semibold">다운로드 (준비 중)</span>
          </button>
        </div>

          <button
            onClick={handleFinalCompose}
            disabled={isPreviewLoading}
            type="button"
            className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all disabled:opacity-70"
            style={{
              background:
                'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
              boxShadow: '0 8px 32px rgba(155, 126, 189, 0.4)',
            }}
          >
            <ArrowRight className="h-5 w-5" />
            완료 (Content_ID 생성 후 NFC 저장)
          </button>
      </div>

      <style>{`
        @keyframes scanLine {
          0% { transform: translateY(-100%); }
          100% { transform: translateY(100%); }
        }

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
