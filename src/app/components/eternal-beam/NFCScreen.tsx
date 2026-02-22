import { ArrowRight, Wifi, Zap, CheckCircle2 } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useState, useEffect, useRef } from 'react'
import { writeToNFCSlot } from '../../services/nfcManager'
import { mapSlotToContent } from '../../services/supabaseContentService'

interface NFCScreenProps {
  onNavigate?: (screen: string) => void
}

export function NFCScreen({ onNavigate }: NFCScreenProps) {
  const { t } = useLanguage()
  const [transferStatus, setTransferStatus] = useState<
    'ready' | 'transferring' | 'complete'
  >('ready')
  const [progress, setProgress] = useState(0)
  const [loading, setLoading] = useState(false)
  const [selectedSlot, setSelectedSlot] = useState(1)
  const [contentId, setContentId] = useState<string | null>(null)
  const progressRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    setContentId(localStorage.getItem('eternal_beam_current_content_id'))
  }, [])

  const handleNFCWrite = async (slotNumber: number) => {
    try {
      setLoading(true)

      const content = localStorage.getItem('eternal_beam_current_content_id')
      const videoId =
        localStorage.getItem('eternal_beam_hologram_video_id') ??
        localStorage.getItem('eternal_beam_current_video_id') ??
        'video_unknown'

      if (!content) {
        alert('콘텐츠 정보를 찾을 수 없습니다. 결제를 먼저 완료해주세요.')
        return
      }

      const result = await writeToNFCSlot(content, videoId, slotNumber)

      if (result.success) {
        await mapSlotToContent(slotNumber, content)
        if (navigator.vibrate) {
          navigator.vibrate([100, 50, 100, 50, 100])
        }
        setTransferStatus('complete')
        setTimeout(() => onNavigate?.('playback'), 1500)
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
            setTimeout(() => onNavigate?.('playback'), 1500)
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
    if (transferStatus !== 'ready') return

    const timer = setTimeout(() => {
      setTransferStatus('transferring')

      let currentProgress = 0
      const interval = setInterval(() => {
        currentProgress += 2
        setProgress(currentProgress)

        if (currentProgress >= 100) {
          clearInterval(interval)
          setTransferStatus('complete')
          setTimeout(() => onNavigate?.('playback'), 1500)
        }
      }, 50)
      progressRef.current = interval
    }, 3000)

    return () => {
      clearTimeout(timer)
      if (progressRef.current) clearInterval(progressRef.current)
    }
  }, [onNavigate, transferStatus])

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)',
      }}
    >
      {/* Animated Background Waves */}
      <div className="absolute inset-0 overflow-hidden opacity-30">
        {[...Array(5)].map((_, i) => (
          <div
            key={i}
            className="absolute rounded-full"
            style={{
              width: `${300 + i * 100}px`,
              height: `${300 + i * 100}px`,
              border: '2px solid rgba(255, 255, 255, 0.3)',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              animation: `ripple ${2 + i * 0.5}s ease-out infinite`,
              animationDelay: `${i * 0.3}s`,
            }}
          />
        ))}
      </div>

      {/* Header */}
      <div className="glass-dark sticky top-0 z-10 flex justify-end px-6 py-4 rounded-b-2xl border-0">
        <button
          onClick={() => onNavigate?.('playback')}
          className="glass-dark flex h-10 w-10 items-center justify-center rounded-full border border-white/20"
        >
          <ArrowRight className="h-5 w-5 text-white" />
        </button>
      </div>

      {/* Main Content */}
      <div className="relative z-10 -mt-20 flex h-full flex-col items-center justify-center px-6">
        {/* NFC Icon */}
        <div className="glass-strong relative mb-8 flex h-40 w-40 items-center justify-center rounded-full">
          {transferStatus === 'complete' ? (
            <CheckCircle2 className="h-20 w-20 text-white" />
          ) : (
            <Wifi
              className="h-20 w-20 text-white"
              style={{
                animation:
                  transferStatus === 'transferring'
                    ? 'pulse 1s ease-in-out infinite'
                    : 'none',
              }}
            />
          )}

          {transferStatus === 'transferring' && (
            <>
              <div
                className="absolute inset-0 rounded-full border-2 border-white"
                style={{
                  animation: 'ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite',
                }}
              />
              <div
                className="absolute inset-0 rounded-full border-2 border-white"
                style={{
                  animation: 'ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite',
                  animationDelay: '0.5s',
                }}
              />
            </>
          )}
        </div>

        {/* Title */}
        <h1
          className="font-headline mb-4 text-center text-3xl font-bold text-white"
        >
          {transferStatus === 'complete'
            ? t('nfc.complete')
            : 'NFC 슬롯에 전송'}
        </h1>

        {/* Description */}
        <p className="mb-8 max-w-xs text-center text-white/80">
          {transferStatus === 'ready' && t('nfc.desc')}
          {transferStatus === 'transferring' && t('nfc.transferring')}
          {transferStatus === 'complete' &&
            '홀로그램이 성공적으로 전송되었습니다!'}
        </p>

        {/* Progress Bar */}
        {transferStatus === 'transferring' && (
          <div className="mb-8 w-full max-w-xs">
            <div
              className="h-2 overflow-hidden rounded-full"
              style={{
                background: 'rgba(255, 255, 255, 0.2)',
              }}
            >
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${progress}%`,
                  background:
                    'linear-gradient(90deg, #00d4ff 0%, #ffffff 100%)',
                  boxShadow: '0 0 20px rgba(0, 212, 255, 0.8)',
                }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between">
              <span className="text-xs text-white/60">전송 중...</span>
              <span className="text-sm font-bold text-white">
                {progress}%
              </span>
            </div>
          </div>
        )}

        {/* Transfer Details */}
        {transferStatus === 'transferring' && (
          <div
            className="mb-8 w-full max-w-xs rounded-2xl p-4"
            style={{
              background: 'rgba(255, 255, 255, 0.1)',
              backdropFilter: 'blur(10px)',
              border: '1px solid rgba(255, 255, 255, 0.2)',
            }}
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-white/70">홀로그램 데이터</span>
              <span className="text-sm font-semibold text-white">
                24.5 MB
              </span>
            </div>
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm text-white/70">음성 메시지</span>
              <span className="text-sm font-semibold text-white">
                1.2 MB
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-sm text-white/70">테마 에셋</span>
              <span className="text-sm font-semibold text-white">
                3.8 MB
              </span>
            </div>
          </div>
        )}

        {/* Status Indicator */}
        <div
          className="mt-8 flex items-center gap-2 rounded-full px-4 py-2"
          style={{
            background: 'rgba(255, 255, 255, 0.2)',
            backdropFilter: 'blur(10px)',
          }}
        >
          <Zap className="h-4 w-4 text-white" />
          <span className="text-sm font-semibold text-white">
            {transferStatus === 'ready' && t('nfc.ready')}
            {transferStatus === 'transferring' && 'NFC 연결됨'}
            {transferStatus === 'complete' && '전송 완료'}
          </span>
        </div>

        {/* Slot Selection & NFC Write - when ready */}
        {transferStatus === 'ready' && (
          <div className="mt-8 flex flex-col items-center gap-4">
            <div className="flex gap-2">
              {[1, 2, 3, 4, 5].map((slot) => (
                <button
                  key={slot}
                  onClick={() => setSelectedSlot(slot)}
                  className="flex h-12 w-12 items-center justify-center rounded-xl font-bold text-white transition-all"
                  style={{
                    background:
                      selectedSlot === slot
                        ? 'rgba(255, 255, 255, 0.4)'
                        : 'rgba(255, 255, 255, 0.2)',
                    border: '2px solid rgba(255, 255, 255, 0.4)',
                  }}
                >
                  {slot}
                </button>
              ))}
            </div>
            <button
              onClick={() => handleNFCWrite(selectedSlot)}
              disabled={loading || !contentId}
              className="rounded-2xl px-8 py-3 font-bold text-white disabled:opacity-50"
              style={{
                background: 'rgba(255, 255, 255, 0.3)',
                border: '2px solid rgba(255, 255, 255, 0.5)',
              }}
            >
              {loading ? 'NFC 쓰는 중...' : '슬롯에 전송하기'}
            </button>
            {!contentId && (
              <p
                className="text-center text-sm"
                style={{ color: 'rgba(255, 200, 200, 0.9)' }}
              >
                ⚠️ 결제를 먼저 완료해주세요
              </p>
            )}
            <button
              onClick={() => onNavigate?.('home')}
              className="rounded-full px-6 py-2 text-sm font-semibold"
              style={{
                background: 'rgba(255, 255, 255, 0.15)',
                border: '1px solid rgba(255, 255, 255, 0.3)',
                color: 'white',
              }}
            >
              나중에
            </button>
          </div>
        )}
      </div>

      <style>{`
        @keyframes ripple {
          0% { transform: translate(-50%, -50%) scale(0.8); opacity: 1; }
          100% { transform: translate(-50%, -50%) scale(1.5); opacity: 0; }
        }
        @keyframes ping {
          0% { transform: scale(1); opacity: 1; }
          75%, 100% { transform: scale(2); opacity: 0; }
        }
      `}</style>
    </div>
  )
}
