import { HardDrive, Film } from 'lucide-react'
import type { UserQuota } from '../../services/quotaService'
import { PLAN_NAMES } from '../../services/quotaService'

interface UsageGaugeProps {
  quota: UserQuota | null
  onUpgrade?: () => void
}

export function UsageGauge({ quota, onUpgrade }: UsageGaugeProps) {
  if (!quota) return null

  const storagePct = Math.min(100, (quota.storage_usage_bytes / quota.max_storage_bytes) * 100)
  const genPct = Math.min(100, (quota.generation_count_this_month / quota.max_generations) * 100)

  return (
    <div className="glass rounded-2xl p-4 space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-gray-800">이번 달 사용량</h3>
        <span className="text-xs font-medium text-purple-600">{PLAN_NAMES[quota.plan_type]}</span>
      </div>

      {/* 생성 횟수 */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="flex items-center gap-1">
            <Film className="h-3.5 w-3" />
            영상 생성
          </span>
          <span className={quota.is_over_generation ? 'text-red-600 font-semibold' : 'text-gray-600'}>
            {quota.generation_count_this_month} / {quota.max_generations}회
          </span>
        </div>
        <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              quota.is_over_generation ? 'bg-red-500' : genPct > 80 ? 'bg-amber-500' : 'bg-[#667eea]'
            }`}
            style={{ width: `${genPct}%` }}
          />
        </div>
      </div>

      {/* 저장 용량 */}
      <div>
        <div className="flex justify-between text-xs mb-1">
          <span className="flex items-center gap-1">
            <HardDrive className="h-3.5 w-3" />
            저장 공간
          </span>
          <span className={quota.is_over_storage ? 'text-red-600 font-semibold' : 'text-gray-600'}>
            {quota.storage_used_gb.toFixed(2)} / {quota.storage_limit_gb}GB
          </span>
        </div>
        <div className="h-2 rounded-full bg-gray-200 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              quota.is_over_storage ? 'bg-red-500' : storagePct > 80 ? 'bg-amber-500' : 'bg-[#667eea]'
            }`}
            style={{ width: `${storagePct}%` }}
          />
        </div>
      </div>

      {(quota.is_over_generation || quota.is_over_storage) && onUpgrade && (
        <button
          type="button"
          onClick={onUpgrade}
          className="w-full py-2 rounded-xl text-sm font-semibold text-white bg-gradient-to-r from-[#667eea] to-[#764ba2]"
        >
          플랜 업그레이드
        </button>
      )}
    </div>
  )
}
