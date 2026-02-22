import {
  ArrowRight,
  Wifi,
  WifiOff,
  Lock,
  Check,
  Loader2,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useState, useEffect } from 'react'

interface DeviceConnectionScreenProps {
  onNavigate?: (screen: string) => void
}

const MOCK_WIFI_LIST = [
  { ssid: 'EternalBeam_5G', signal: 100, secured: true },
  { ssid: 'EternalBeam_2.4G', signal: 85, secured: true },
  { ssid: 'Home_WiFi', signal: 72, secured: true },
  { ssid: 'Guest_Network', signal: 45, secured: false },
]

export function DeviceConnectionScreen({ onNavigate }: DeviceConnectionScreenProps) {
  const { t } = useLanguage()
  const [selectedWifi, setSelectedWifi] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [status, setStatus] = useState<'idle' | 'connecting' | 'connected'>('idle')

  const handleConnect = () => {
    if (!selectedWifi) return
    setStatus('connecting')
    setTimeout(() => {
      setStatus('connected')
      setTimeout(() => onNavigate?.('home'), 1500)
    }, 2500)
  }

  const handleSkip = () => {
    onNavigate?.('home')
  }

  const getSignalBars = (signal: number) => {
    if (signal >= 80) return '▂▄▆█'
    if (signal >= 60) return '▂▄▆▁'
    if (signal >= 40) return '▂▄▁▁'
    return '▂▁▁▁'
  }

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1
            className="font-headline text-2xl font-bold"
            style={{ color: '#2d3748' }}
          >
            Wi-Fi 설정
          </h1>
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#718096' }}>
          기기에 연결할 Wi-Fi를 선택하세요
        </p>
      </div>

      {/* Main Content */}
      <div className="px-6 py-6">
        {/* Connecting Loading State */}
        {status === 'connecting' && (
          <div
            className="mb-6 flex flex-col items-center justify-center rounded-2xl p-8"
            style={{
              background: 'white',
              boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
            }}
          >
            <div
              className="mb-4 flex h-20 w-20 items-center justify-center rounded-full"
              style={{
                background: 'linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(0, 212, 255, 0.1) 100%)',
              }}
            >
              <Loader2
                className="h-10 w-10 text-[#667eea]"
                style={{ animation: 'spin 1s linear infinite' }}
              />
            </div>
            <p className="font-semibold" style={{ color: '#2d3748' }}>
              연결 중...
            </p>
            <p className="mt-1 text-sm" style={{ color: '#718096' }}>
              Wi-Fi에 연결하고 있습니다
            </p>
          </div>
        )}

        {/* Connected Success State */}
        {status === 'connected' && (
          <div
            className="mb-6 flex flex-col items-center justify-center rounded-2xl p-8"
            style={{
              background: 'linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(0, 212, 255, 0.1) 100%)',
              boxShadow: '0 4px 16px rgba(102, 126, 234, 0.2)',
              animation: 'scaleIn 0.5s ease-out',
            }}
          >
            <div
              className="mb-4 flex h-20 w-20 items-center justify-center rounded-full"
              style={{
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                boxShadow: '0 8px 24px rgba(102, 126, 234, 0.4)',
              }}
            >
              <Check className="h-10 w-10 text-white" />
            </div>
            <p className="text-xl font-bold" style={{ color: '#2d3748' }}>
              연결 완료!
            </p>
            <p className="mt-1 text-sm" style={{ color: '#718096' }}>
              기기가 Wi-Fi에 연결되었습니다
            </p>
          </div>
        )}

        {/* Wi-Fi List */}
        {status === 'idle' && (
          <>
            <div
              className="mb-6 rounded-2xl overflow-hidden"
              style={{
                background: 'white',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.08)',
              }}
            >
              {MOCK_WIFI_LIST.map((wifi) => (
                <button
                  key={wifi.ssid}
                  onClick={() => setSelectedWifi(wifi.ssid)}
                  className="flex w-full items-center gap-4 px-4 py-4 transition-colors hover:bg-gray-50"
                  style={{
                    borderBottom: '1px solid rgba(0, 0, 0, 0.05)',
                  }}
                >
                  <div
                    className="flex h-10 w-10 items-center justify-center rounded-full"
                    style={{
                      background: selectedWifi === wifi.ssid
                        ? 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
                        : 'rgba(102, 126, 234, 0.1)',
                    }}
                  >
                    <Wifi
                      className="h-5 w-5"
                      style={{
                        color: selectedWifi === wifi.ssid ? 'white' : '#667eea',
                      }}
                    />
                  </div>
                  <div className="flex-1 text-left">
                    <p className="font-semibold" style={{ color: '#2d3748' }}>
                      {wifi.ssid}
                    </p>
                    <p className="text-xs" style={{ color: '#718096' }}>
                      {getSignalBars(wifi.signal)} {wifi.signal}% •{' '}
                      {wifi.secured ? '보안됨' : '열림'}
                    </p>
                  </div>
                  {wifi.secured && (
                    <Lock className="h-4 w-4" style={{ color: '#a0aec0' }} />
                  )}
                </button>
              ))}
            </div>

            {/* Password Input */}
            {selectedWifi && (
              <div className="mb-6">
                <label className="mb-2 block text-sm font-semibold" style={{ color: '#4a5568' }}>
                  비밀번호
                </label>
                <div className="relative">
                  <Lock className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-[#a0aec0]" />
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Wi-Fi 비밀번호 입력"
                    className="w-full rounded-xl border border-gray-200 py-3 pl-12 pr-4 outline-none focus:border-[#667eea] focus:ring-2 focus:ring-[#667eea]/20"
                  />
                </div>
              </div>
            )}

            {/* Connect Button */}
            <button
              onClick={handleConnect}
              disabled={!selectedWifi}
              className="mb-4 flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all disabled:opacity-50"
              style={{
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
              }}
            >
              <Wifi className="h-5 w-5" />
              연결하기
            </button>

            {/* Skip Button */}
            <button
              onClick={handleSkip}
              className="w-full rounded-2xl py-3 text-sm font-semibold"
              style={{
                background: 'rgba(102, 126, 234, 0.1)',
                color: '#667eea',
              }}
            >
              건너뛰기
            </button>
          </>
        )}
      </div>

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
        @keyframes scaleIn {
          0% { transform: scale(0.9); opacity: 0; }
          100% { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
