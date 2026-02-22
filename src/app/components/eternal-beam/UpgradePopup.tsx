import { X, Zap, Check } from 'lucide-react'
import { PLAN_NAMES, PLAN_PRICES, type PlanType } from '../../services/quotaService'

interface UpgradePopupProps {
  reason: 'generation' | 'storage'
  onClose: () => void
  onSelectPlan?: (plan: PlanType) => void
}

export function UpgradePopup({ reason, onClose, onSelectPlan }: UpgradePopupProps) {
  const message =
    reason === 'generation'
      ? '이번 달 영상 생성 횟수를 모두 사용했습니다.'
      : '저장 공간이 부족합니다.'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="glass-strong w-full max-w-sm rounded-2xl overflow-hidden shadow-xl">
        <div className="p-6">
          <div className="flex justify-between items-start mb-4">
            <h2 className="text-lg font-bold text-gray-800">플랜 업그레이드</h2>
            <button
              type="button"
              onClick={onClose}
              className="p-1 rounded-full hover:bg-gray-100"
            >
              <X className="h-5 w-5 text-gray-500" />
            </button>
          </div>
          <p className="text-sm text-gray-600 mb-6">{message}</p>

          <div className="space-y-2">
            {(['basic', 'premium', 'lifetime'] as PlanType[]).map((plan) => (
              <button
                key={plan}
                type="button"
                onClick={() => onSelectPlan?.(plan)}
                className="w-full flex items-center justify-between rounded-xl p-4 border-2 border-gray-200 hover:border-[#667eea] transition-colors text-left"
              >
                <div>
                  <p className="font-bold text-gray-800">{PLAN_NAMES[plan]}</p>
                  <p className="text-xs text-gray-500">
                    {plan === 'basic' && '월 10회 · 5GB'}
                    {plan === 'premium' && '월 30회 · 20GB · 1080p'}
                    {plan === 'lifetime' && '월 30회 · 50GB · 평생 보존'}
                  </p>
                </div>
                <span className="font-bold text-[#667eea]">
                  {PLAN_PRICES[plan].monthly
                    ? `월 ₩${PLAN_PRICES[plan].monthly!.toLocaleString()}`
                    : `₩${PLAN_PRICES[plan].oneTime!.toLocaleString()}`}
                </span>
              </button>
            ))}
          </div>

          <button
            type="button"
            onClick={onClose}
            className="mt-4 w-full py-2 text-sm text-gray-500"
          >
            닫기
          </button>
        </div>
      </div>
    </div>
  )
}
