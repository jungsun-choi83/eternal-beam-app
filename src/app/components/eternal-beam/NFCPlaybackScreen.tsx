import { useState, useEffect, useRef } from 'react'
import { ArrowRight, Wifi, Zap, CheckCircle2, Home, Plus, Play } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { writeToNFCSlot } from '../../services/nfcManager'
import { mapSlotToContent } from '../../services/supabaseContentService'
import { isVideoUrl } from '../../utils/mediaType'

type Phase = 'nfc' | 'playback'

interface NFCPlaybackScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

const DEPTH_INTENSITY = 1.5

export function NFCPlaybackScreen({ onNavigate, onBack }: NFCPlaybackScreenProps) {
  const { t } = useLanguage()
  const { selectedImage } = useImage()
  const [phase, setPhase] = useState<Phase>('nfc')
  const [transferStatus, setTransferStatus] = useState<'ready' | 'transferring' | 'complete'>('ready')
  const [progress, setProgress] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selectedSlot, setSelectedSlot] = useState(1)
  const [contentId, setContentId] = useState<string | null>(null)
  const [composedPreview, setComposedPreview] = useState<string | null>(null)
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    setContentId(localStorage.getItem('eternal_beam_content_id') || localStorage.getItem('eternal_beam_current_content_id'))
    setComposedPreview(
      localStorage.getItem('eternal_beam_composed_video_url') ||
      localStorage.getItem('eternal_beam_composed_preview') ||
      localStorage.getItem('eternal_beam_main_video_url')
    )
  }, [])

  const displaySrc = composedPreview ?? selectedImage ?? null
  const isVideo = displaySrc ? isVideoUrl(displaySrc) : false

  const handleNFCWrite = async (slotNumber: number) => {
    try {
      setLoading(true)
      const cid = contentId || localStorage.getItem('eternal_beam_content_id') || localStorage.getItem('eternal_beam_current_content_id')

      if (!cid) {
        alert('먼저 미리보기에서 "완료"를 눌러 Content_ID를 생성해주세요.')
        return
      }

      const videoId = localStorage.getItem('eternal_beam_hologram_video_id') ?? localStorage.getItem('eternal_beam_current_video_id') ?? 'video_unknown'
      const payloadForNfc = { content_id: cid, slot_number: slotNumber }

      const result = await writeToNFCSlot(cid, videoId, slotNumber, payloadForNfc)

      if (result.success) {
        await mapSlotToContent(slotNumber, cid)
        if (navigator.vibrate) navigator.vibrate([100, 50, 100, 50, 100])
        setTransferStatus('complete')
        setTimeout(() => setPhase('playback'), 800)
      } else {
        setTransferStatus('transferring')
        setProgress(0)
        let currentProgress = 0
        const interval = setInterval(() => {
          currentProgress += 2
          setProgress(currentProgress)
          if (currentProgress >= 100) {
            clearInterval(interval)
            setTransferStatus('complete')
            setTimeout(() => setPhase('playback'), 800)
          }
        }, 50)
      }
    } catch (error: unknown) {
      const err = error as Error
      alert(err.message || 'NFC 쓰기에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (phase !== 'nfc' || transferStatus !== 'ready') return
    const timer = setTimeout(() => {
      setTransferStatus('transferring')
      let currentProgress = 0
      const interval = setInterval(() => {
        currentProgress += 2
        setProgress(currentProgress)
        if (currentProgress >= 100) {
          clearInterval(interval)
          setTransferStatus('complete')
          setTimeout(() => setPhase('playback'), 800)
        }
      }, 50)
      progressRef.current = interval
    }, 3000)
    return () => {
      clearTimeout(timer)
      if (progressRef.current) clearInterval(progressRef.current)
    }
  }, [phase, transferStatus])

  if (phase === 'playback') {
    return (
      <div
        className="relative h-full w-full overflow-hidden"
        style={{ background: '#000000' }}
      >
        <div className="flex h-full flex-col items-center justify-center px-6 py-20">
          <div
            className="relative mb-8 flex h-56 w-56 items-center justify-center overflow-hidden rounded-3xl"
            style={{
              background: displaySrc
                ? 'transparent'
                : 'linear-gradient(135deg, rgba(0,212,255,0.2) 0%, rgba(118,75,162,0.2) 100%)',
              border: '1px solid rgba(255,255,255,0.1)',
              boxShadow: '0 0 60px rgba(0,212,255,0.2)',
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
                  alt="합성 미리보기"
                  className="h-full w-full object-cover"
                  style={{
                    filter: `brightness(${0.7 + DEPTH_INTENSITY * 0.1}) contrast(1.1)`,
                    mixBlendMode: 'screen',
                  }}
                />
              )
            ) : (
              <span className="text-6xl">✨</span>
            )}
            <div
              className="absolute inset-0 flex items-center justify-center rounded-3xl"
              style={{ background: 'rgba(0,0,0,0.2)', opacity: 0.8 }}
            >
              <div
                className="flex h-20 w-20 items-center justify-center rounded-full"
                style={{
                  background: 'rgba(255,255,255,0.15)',
                  backdropFilter: 'blur(10px)',
                }}
              >
                <Play className="h-10 w-10 ml-1 text-white" fill="white" />
              </div>
            </div>
          </div>
          <p className="mb-10 text-center text-white/80">
            슬롯에 저장이 완료되었습니다. 아이와의 재회를 준비해 주세요.
          </p>
          <div className="w-full max-w-xs space-y-3">
            <button
              onClick={() => onNavigate?.('home')}
              className="flex w-full items-center justify-center gap-3 rounded-2xl py-4 font-bold text-white transition-opacity hover:opacity-90"
              style={{
                background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 50%, #764ba2 100%)',
                boxShadow: '0 8px 32px rgba(0,212,255,0.3)',
              }}
            >
              <Home className="h-5 w-5" />
              홈으로
            </button>
            <button
              onClick={() => onNavigate?.('photoUpload')}
              className="flex w-full items-center justify-center gap-3 rounded-2xl border border-white/30 py-4 font-bold text-white transition-opacity hover:opacity-90"
            >
              <Plus className="h-5 w-5" />
              새 추억
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{ background: '#000000' }}
    >
      <div className="sticky top-0 z-10 flex justify-end px-6 py-4">
        <button
          onClick={() => (onBack ? onBack() : onNavigate?.('home'))}
          className="rounded-full p-2 text-white/70 transition hover:bg-white/10 hover:text-white"
        >
          <ArrowRight className="h-5 w-5" />
        </button>
      </div>

      <div className="relative z-10 -mt-12 flex flex-col items-center justify-center px-6">
        <div
          className="relative mb-8 flex h-40 w-40 items-center justify-center rounded-full border border-white/10"
          style={{ boxShadow: '0 0 40px rgba(0,212,255,0.2)' }}
        >
          {transferStatus === 'complete' ? (
            <CheckCircle2 className="h-20 w-20 text-cyan-400" />
          ) : (
            <Wifi
              className="h-20 w-20 text-white/90"
              style={{
                animation: transferStatus === 'transferring' ? 'pulse 1s ease-in-out infinite' : 'none',
              }}
            />
          )}
          {transferStatus === 'transferring' && (
            <>
              <div className="absolute inset-0 rounded-full border-2 border-cyan-400/50 animate-ping" />
              <div
                className="absolute inset-0 rounded-full border-2 border-cyan-400/50 animate-ping"
                style={{ animationDelay: '0.5s' }}
              />
            </>
          )}
        </div>

        <h1 className="mb-4 text-center text-2xl font-bold text-white">
          {transferStatus === 'complete' ? t('nfc.complete') : 'NFC 슬롯에 전송'}
        </h1>
        <p className="mb-8 max-w-xs text-center text-sm text-white/70">
          {transferStatus === 'ready' && (t('nfc.desc') || '기기를 NFC에 가져다 대주세요.')}
          {transferStatus === 'transferring' && (t('nfc.transferring') || '전송 중...')}
          {transferStatus === 'complete' && '홀로그램이 슬롯에 저장되었습니다.'}
        </p>

        {transferStatus === 'transferring' && (
          <div className="mb-8 w-full max-w-xs">
            <div className="h-2 overflow-hidden rounded-full bg-white/20">
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${progress}%`,
                  background: 'linear-gradient(90deg, #00d4ff 0%, #fff 100%)',
                  boxShadow: '0 0 20px rgba(0,212,255,0.6)',
                }}
              />
            </div>
            <div className="mt-2 flex justify-between text-xs text-white/60">
              <span>전송 중...</span>
              <span className="font-bold text-white">{progress}%</span>
            </div>
          </div>
        )}

        <div
          className="mb-8 flex items-center gap-2 rounded-full border border-white/20 px-4 py-2"
          style={{ background: 'rgba(255,255,255,0.05)' }}
        >
          <Zap className="h-4 w-4 text-cyan-400" />
          <span className="text-sm font-medium text-white/90">
            {transferStatus === 'ready' && (t('nfc.ready') || '슬롯을 선택 후 전송하세요')}
            {transferStatus === 'transferring' && 'NFC 연결됨'}
            {transferStatus === 'complete' && '전송 완료'}
          </span>
        </div>

        {transferStatus === 'ready' && (
          <div className="mt-4 flex flex-col items-center gap-4">
            <div className="flex gap-2">
              {[1, 2, 3, 4, 5].map((slot) => (
                <button
                  key={slot}
                  onClick={() => setSelectedSlot(slot)}
                  className="flex h-12 w-12 items-center justify-center rounded-xl font-bold text-white transition-all"
                  style={{
                    background: selectedSlot === slot ? 'rgba(0,212,255,0.4)' : 'rgba(255,255,255,0.1)',
                    border: `2px solid ${selectedSlot === slot ? 'rgba(0,212,255,0.6)' : 'rgba(255,255,255,0.2)'}`,
                  }}
                >
                  {slot}
                </button>
              ))}
            </div>
            <button
              onClick={() => handleNFCWrite(selectedSlot)}
              disabled={loading || !contentId}
              className="rounded-2xl px-8 py-3 font-bold text-white transition-opacity disabled:opacity-50"
              style={{
                background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)',
                boxShadow: '0 8px 24px rgba(0,212,255,0.3)',
              }}
            >
              {loading ? 'NFC 쓰는 중...' : '슬롯에 전송하기'}
            </button>
            {!contentId && (
              <p className="text-center text-sm text-amber-300/90">
                미리보기에서 &quot;완료&quot;를 눌러 Content_ID를 생성한 뒤 저장할 수 있습니다.
              </p>
            )}
            <button
              onClick={() => onNavigate?.('home')}
              className="rounded-full border border-white/20 px-6 py-2 text-sm font-medium text-white/80 hover:bg-white/10"
            >
              나중에
            </button>
          </div>
        )}
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50% { opacity: 0.7; transform: scale(1.05); }
        }
      `}</style>
    </div>
  )
}
