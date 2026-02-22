import {
  ArrowRight,
  Camera,
  Smartphone,
  Check,
  Sparkles,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useState, useEffect } from 'react'
import { registerDevice, isDeviceRegistered } from '../../services/firebaseService'
import { auth } from '../../config/firebase'

const DEVICE_REGISTERED_KEY = 'eternal_beam_device_registered'

interface QRConnectionScreenProps {
  onNavigate?: (screen: string) => void
}

const MOCK_DEVICE = {
  model: 'EB-2026',
  serial: 'EB12345',
}

export function QRConnectionScreen({ onNavigate }: QRConnectionScreenProps) {
  const { t } = useLanguage()
  const [isScanning, setIsScanning] = useState(true)
  const [isConnected, setIsConnected] = useState(false)
  const [showSuccess, setShowSuccess] = useState(false)
  const [loading, setLoading] = useState(false)
  const [showSkip, setShowSkip] = useState(false)

  useEffect(() => {
    const checkDevice = async () => {
      const userId = auth.currentUser?.uid
      if (userId) {
        try {
          const registered = await isDeviceRegistered(userId)
          if (registered) setShowSkip(true)
        } catch {
          // ignore
        }
      }
    }
    checkDevice()
  }, [])

  const handleQRScan = async (qrData: string) => {
    try {
      setLoading(true)
      const deviceId = qrData
      const userId = auth.currentUser?.uid

      if (!userId) {
        localStorage.setItem(DEVICE_REGISTERED_KEY, 'true')
        localStorage.setItem('eternal_beam_device_id', deviceId)
        setIsScanning(false)
        setIsConnected(true)
        setShowSuccess(true)
        setTimeout(() => onNavigate?.('deviceConnection'), 1500)
        return
      }

      await registerDevice(deviceId, userId)

      localStorage.setItem(DEVICE_REGISTERED_KEY, 'true')
      localStorage.setItem('eternal_beam_device_id', deviceId)

      setIsScanning(false)
      setIsConnected(true)
      setShowSuccess(true)
      setTimeout(() => onNavigate?.('deviceConnection'), 1500)
    } catch (error: unknown) {
      const err = error as Error
      console.error('기기 등록 실패:', error)
      alert(err.message || '기기 등록에 실패했습니다.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (isScanning && !loading) {
      const timer = setTimeout(() => {
        const mockDeviceId = 'device_EB12345'
        void handleQRScan(mockDeviceId)
      }, 3000)
      return () => clearTimeout(timer)
    }
  }, [isScanning, loading])

  const handleLater = () => {
    onNavigate?.('home')
  }

  return (
    <div
      className="relative h-full w-full overflow-hidden"
      style={{
        background: 'linear-gradient(180deg, #2D2640 0%, #4a3f5c 50%, #3d3550 100%)',
      }}
    >
      {/* Header */}
      <div className="glass-dark sticky top-0 z-10 px-6 py-4 rounded-b-2xl border-0">
        <div className="mb-2 flex items-center justify-between">
          <h1
          className="font-headline mb-2 text-2xl font-bold text-white"
        >
          내 기기 등록
        </h1>
          <button
            onClick={() => onNavigate?.('deviceConnection')}
            className="glass-dark flex h-10 w-10 items-center justify-center rounded-full shrink-0 border border-white/20"
          >
            <ArrowRight className="h-5 w-5 text-white" />
          </button>
        </div>
        <p className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
          기기 바닥의 QR 코드를 스캔해주세요
        </p>
      </div>

      {/* Main Content */}
      <div className="flex flex-col items-center justify-center px-6 py-8">
        {/* QR Scanner Frame with Guide Animation */}
        <div className="relative mb-8">
          <div
            className="glass-dark relative h-64 w-64 overflow-hidden rounded-3xl border-2 border-white/20"
            style={{
              boxShadow: '0 0 40px rgba(155, 126, 189, 0.3)',
            }}
          >
            {/* Corner Brackets - Pulsing Guide Animation */}
            {[
              { top: 0, left: 0, rotate: 0 },
              { top: 0, right: 0, rotate: 90 },
              { bottom: 0, right: 0, rotate: 180 },
              { bottom: 0, left: 0, rotate: 270 },
            ].map((pos, i) => (
              <div
                key={i}
                className="absolute h-10 w-10"
                style={{
                  top: pos.top,
                  left: pos.left,
                  right: pos.right,
                  bottom: pos.bottom,
                  borderTop: '4px solid #00d4ff',
                  borderLeft: '4px solid #00d4ff',
                  transform: `rotate(${pos.rotate}deg)`,
                  animation: isScanning ? 'pulse-border 1.5s ease-in-out infinite' : 'none',
                }}
              />
            ))}

            {/* Camera View / Success Content */}
            <div className="absolute inset-0 flex flex-col items-center justify-center p-4">
              {!isConnected ? (
                <>
                  <div
                    className="mb-4 flex h-20 w-20 items-center justify-center rounded-full"
                    style={{
                      background: 'rgba(0, 212, 255, 0.1)',
                      animation: 'breathe 2s ease-in-out infinite',
                    }}
                  >
                    <Camera
                      className="h-12 w-12"
                      style={{ color: 'rgba(255, 255, 255, 0.5)' }}
                    />
                  </div>
                  <p className="text-center text-xs" style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                    기기 바닥의 QR 코드를
                    <br />
                    카메라에 비춰주세요
                  </p>
                </>
              ) : showSuccess ? (
                <div
                  className="relative flex flex-col items-center"
                  style={{ animation: 'scaleIn 0.5s ease-out' }}
                >
                  <div
                    className="mb-4 flex h-24 w-24 items-center justify-center rounded-full"
                    style={{
                      background: 'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)',
                      boxShadow: '0 0 30px rgba(0, 212, 255, 0.6)',
                    }}
                  >
                    <Check className="h-14 w-14 text-white" />
                  </div>
                  <span
                    className="text-xl font-bold text-white"
                    style={{ animation: 'fadeIn 0.5s ease-out' }}
                  >
                    등록 완료!
                  </span>
                  {[...Array(6)].map((_, i) => (
                    <Sparkles
                      key={i}
                      className="absolute h-5 w-5 text-yellow-400"
                      style={{
                        top: '50%',
                        left: '50%',
                        transform: `translate(-50%, -50%) rotate(${i * 60}deg) translateY(-70px)`,
                        animation: `sparkle 1s ease-out ${i * 0.1}s forwards`,
                        opacity: 0,
                      }}
                    />
                  ))}
                </div>
              ) : null}
            </div>

            {/* Scanning Line Animation */}
            {isScanning && !isConnected && (
              <div
                className="absolute left-0 right-0 h-1"
                style={{
                  background: 'linear-gradient(90deg, transparent, #00d4ff, transparent)',
                  boxShadow: '0 0 20px rgba(0, 212, 255, 0.8)',
                  animation: 'scan 2s linear infinite',
                }}
              />
            )}

            {/* Grid Overlay */}
            {isScanning && (
              <div
                className="absolute inset-0 pointer-events-none"
                style={{
                  backgroundImage: `
                    linear-gradient(rgba(102, 126, 234, 0.08) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(102, 126, 234, 0.08) 1px, transparent 1px)
                  `,
                  backgroundSize: '20px 20px',
                }}
              />
            )}
          </div>

          {/* Guide Text */}
          {isScanning && (
            <p
              className="mt-6 max-w-xs text-center text-sm"
              style={{ color: 'rgba(255, 255, 255, 0.8)' }}
            >
              기기 바닥의 QR 코드를 카메라에 비춰주세요
            </p>
          )}

          {/* Success - Device Info */}
          {isConnected && showSuccess && (
            <div
              className="mt-6 w-full rounded-2xl p-4"
              style={{
                background: 'rgba(255, 255, 255, 0.08)',
                backdropFilter: 'blur(10px)',
                border: '1px solid rgba(255, 255, 255, 0.15)',
                animation: 'slideUp 0.5s ease-out',
              }}
            >
              <div className="flex items-center gap-4">
                <div
                  className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full"
                  style={{
                    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                  }}
                >
                  <Smartphone className="h-6 w-6 text-white" />
                </div>
                <div className="flex-1">
                  <h3 className="font-bold text-white">
                    Eternal Beam {MOCK_DEVICE.model}
                  </h3>
                  <p className="text-xs" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
                    시리얼 번호: #{MOCK_DEVICE.serial}
                  </p>
                </div>
                <div
                  className="h-3 w-3 rounded-full"
                  style={{
                    background: '#00d4ff',
                    boxShadow: '0 0 10px rgba(0, 212, 255, 0.8)',
                  }}
                />
              </div>
            </div>
          )}
        </div>

        {/* 나중에 Button */}
        {isScanning && (
          <button
            onClick={handleLater}
            className="rounded-full px-6 py-3 text-sm font-semibold"
            style={{
              background: 'rgba(255, 255, 255, 0.1)',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              color: 'white',
            }}
          >
            나중에
          </button>
        )}
      </div>

      <style>{`
        @keyframes scan {
          0% { top: 0; }
          100% { top: 100%; }
        }
        @keyframes pulse-border {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes breathe {
          0%, 100% { transform: scale(1); opacity: 0.8; }
          50% { transform: scale(1.05); opacity: 1; }
        }
        @keyframes scaleIn {
          0% { transform: scale(0); opacity: 0; }
          100% { transform: scale(1); opacity: 1; }
        }
        @keyframes fadeIn {
          0% { opacity: 0; }
          100% { opacity: 1; }
        }
        @keyframes sparkle {
          0% { opacity: 0; transform: translate(-50%, -50%) rotate(var(--r, 0deg)) translateY(-70px) scale(0); }
          50% { opacity: 1; transform: translate(-50%, -50%) rotate(var(--r, 0deg)) translateY(-80px) scale(1); }
          100% { opacity: 0; transform: translate(-50%, -50%) rotate(var(--r, 0deg)) translateY(-90px) scale(0.5); }
        }
        @keyframes slideUp {
          0% { transform: translateY(20px); opacity: 0; }
          100% { transform: translateY(0); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
