import {
  ArrowRight,
  Camera,
  QrCode,
  Smartphone,
  Check,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useState, useEffect } from 'react'

interface QRCodeScreenProps {
  onNavigate?: (screen: string) => void
}

export function QRCodeScreen({ onNavigate }: QRCodeScreenProps) {
  const { t } = useLanguage()
  const [isScanning, setIsScanning] = useState(true)
  const [isConnected, setIsConnected] = useState(false)

  useEffect(() => {
    if (isScanning) {
      const timer = setTimeout(() => {
        setIsScanning(false)
        setIsConnected(true)
        setTimeout(() => {
          onNavigate?.('nfc')
        }, 2000)
      }, 3000)
      return () => clearTimeout(timer)
    }
  }, [isScanning, onNavigate])

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
          style={{
          }}
        >
          {t('qr.title')}
        </h1>
          <button
            onClick={() => onNavigate?.('nfc')}
            className="glass-dark flex h-10 w-10 items-center justify-center rounded-full shrink-0 border border-white/20"
          >
            <ArrowRight className="h-5 w-5 text-white" />
          </button>
        </div>
        <p className="text-sm" style={{ color: 'rgba(255, 255, 255, 0.7)' }}>
          {t('qr.desc')}
        </p>
      </div>

      {/* Main Content */}
      <div className="flex flex-col items-center justify-center px-6 py-12">
        {/* QR Scanner Frame */}
        <div className="relative mb-8">
          <div
            className="glass-dark relative h-64 w-64 overflow-hidden rounded-3xl border-2 border-white/20"
            style={{
              boxShadow: '0 0 40px rgba(155, 126, 189, 0.3)',
            }}
          >
            {/* Corner Brackets */}
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
                }}
              />
            ))}

            {/* Camera View Effect */}
            <div className="absolute inset-0 flex items-center justify-center">
              {!isConnected ? (
                <Camera
                  className="h-20 w-20"
                  style={{ color: 'rgba(255, 255, 255, 0.3)' }}
                />
              ) : (
                <div
                  className="flex h-20 w-20 items-center justify-center rounded-full"
                  style={{
                    background:
                      'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)',
                    animation: 'scaleIn 0.3s ease-out',
                  }}
                >
                  <Check className="h-12 w-12 text-white" />
                </div>
              )}
            </div>

            {/* Scanning Line */}
            {isScanning && !isConnected && (
              <div
                className="absolute left-0 right-0 h-1"
                style={{
                  background:
                    'linear-gradient(90deg, transparent, #00d4ff, transparent)',
                  boxShadow: '0 0 20px rgba(0, 212, 255, 0.8)',
                  animation: 'scan 2s linear infinite',
                }}
              />
            )}

            {/* Grid Overlay */}
            <div
              className="absolute inset-0"
              style={{
                backgroundImage: `
                  linear-gradient(rgba(102, 126, 234, 0.1) 1px, transparent 1px),
                  linear-gradient(90deg, rgba(102, 126, 234, 0.1) 1px, transparent 1px)
                `,
                backgroundSize: '20px 20px',
              }}
            />
          </div>

          {/* Status Indicator */}
          <div
            className="absolute -bottom-6 left-1/2 flex -translate-x-1/2 items-center gap-2 rounded-full px-6 py-2"
            style={{
              background: isConnected
                ? 'linear-gradient(135deg, #00d4ff 0%, #667eea 100%)'
                : 'rgba(255, 255, 255, 0.1)',
              backdropFilter: 'blur(10px)',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.3)',
            }}
          >
            {isConnected ? (
              <>
                <Check className="h-4 w-4 text-white" />
                <span className="text-sm font-bold text-white">
                  {t('qr.connected')}
                </span>
              </>
            ) : (
              <>
                <div className="h-2 w-2 animate-pulse rounded-full bg-white" />
                <span className="text-sm font-bold text-white">
                  {t('qr.scanning')}
                </span>
              </>
            )}
          </div>
        </div>

        {/* Device Info */}
        <div
          className="mt-16 w-full rounded-2xl p-6"
          style={{
            background: 'rgba(255, 255, 255, 0.05)',
            backdropFilter: 'blur(10px)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
          }}
        >
          <div className="flex items-center gap-4">
            <div
              className="flex h-12 w-12 items-center justify-center rounded-full"
              style={{
                background:
                  'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              }}
            >
              <Smartphone className="h-6 w-6 text-white" />
            </div>
            <div className="flex-1">
              <h3 className="mb-1 font-bold text-white">
                Eternal Beam Device
              </h3>
              <p className="text-xs" style={{ color: 'rgba(255, 255, 255, 0.6)' }}>
                Model: EB-2026 • Serial: #EB12345
              </p>
            </div>
            {isConnected && (
              <div
                className="h-3 w-3 rounded-full"
                style={{
                  background: '#00d4ff',
                  boxShadow: '0 0 10px rgba(0, 212, 255, 0.8)',
                }}
              />
            )}
          </div>
        </div>

        {/* Manual Entry */}
        {!isConnected && (
          <button
            className="mt-6 rounded-full px-6 py-3 text-sm font-semibold"
            style={{
              background: 'rgba(255, 255, 255, 0.1)',
              border: '1px solid rgba(255, 255, 255, 0.2)',
              color: 'white',
            }}
          >
            <QrCode className="mr-2 inline h-4 w-4" />
            {t('qr.manual')}
          </button>
        )}
      </div>

      <style>{`
        @keyframes scan {
          0% { top: 0; }
          100% { top: 100%; }
        }
        @keyframes scaleIn {
          0% { transform: scale(0); }
          100% { transform: scale(1); }
        }
      `}</style>
    </div>
  )
}
