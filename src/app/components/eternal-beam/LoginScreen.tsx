import { ArrowRight, Mail, Lock, ChevronRight } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useState } from 'react'

const DEVICE_REGISTERED_KEY = 'eternal_beam_device_registered'

interface LoginScreenProps {
  onNavigate?: (screen: string) => void
}

export function LoginScreen({ onNavigate }: LoginScreenProps) {
  const { t } = useLanguage()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const isDeviceRegistered = localStorage.getItem(DEVICE_REGISTERED_KEY) === 'true'
    if (isDeviceRegistered) {
      onNavigate?.('home')
    } else {
      onNavigate?.('qrConnection')
    }
  }

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="font-headline text-2xl font-bold" style={{ color: '#2D2640' }}>
          로그인
        </h1>
          <button
            onClick={() => {
              const isDeviceRegistered = localStorage.getItem(DEVICE_REGISTERED_KEY) === 'true'
              onNavigate?.(isDeviceRegistered ? 'home' : 'qrConnection')
            }}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>

        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          이터널빔에 오신 것을 환영합니다
        </p>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="px-6 py-6">
        <div className="glass rounded-2xl p-6 mb-6">
        <div className="mb-4">
          <label className="mb-2 block text-sm font-semibold" style={{ color: '#2D2640' }}>
            이메일
          </label>
          <div className="relative">
            <Mail className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-[#a0aec0]" />
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="이메일을 입력하세요"
              className="glass w-full rounded-xl border border-white/30 py-3 pl-12 pr-4 outline-none focus:border-[#9B7EBD] focus:ring-2 focus:ring-[#9B7EBD]/30"
              required
            />
          </div>
        </div>

        <div className="mb-6">
          <label className="mb-2 block text-sm font-semibold" style={{ color: '#2D2640' }}>
            비밀번호
          </label>
          <div className="relative">
            <Lock className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-[#a0aec0]" />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="비밀번호를 입력하세요"
              className="glass w-full rounded-xl border border-white/30 py-3 pl-12 pr-4 outline-none focus:border-[#9B7EBD] focus:ring-2 focus:ring-[#9B7EBD]/30"
              required
            />
          </div>
        </div>
        </div>

        <button
          type="submit"
            className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all"
          style={{
            background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
          }}
        >
          로그인
          <ChevronRight className="h-5 w-5" />
        </button>

        <p className="mt-6 text-center text-sm" style={{ color: '#718096' }}>
          계정이 없으신가요?{' '}
          <button
            type="button"
            onClick={() => onNavigate?.('signup')}
            className="font-semibold"
            style={{ color: '#9B7EBD' }}
          >
            회원가입
          </button>
        </p>
      </form>
    </div>
  )
}
