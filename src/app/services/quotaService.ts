/**
 * 사용자 할당량 서비스 (저장 용량, 생성 횟수)
 */

import { supabase } from '../config/supabase'

export type PlanType = 'basic' | 'premium' | 'lifetime'

export const PLAN_LIMITS: Record<PlanType, { generations: number; storageGB: number; maxHeight: number }> = {
  basic: { generations: 10, storageGB: 5, maxHeight: 720 },
  premium: { generations: 30, storageGB: 20, maxHeight: 1080 },
  lifetime: { generations: 30, storageGB: 50, maxHeight: 1080 },
}

export const PLAN_NAMES: Record<PlanType, string> = {
  basic: '베이직',
  premium: '프리미엄',
  lifetime: '평생 소장권',
}

export const PLAN_PRICES: Record<PlanType, { monthly?: number; oneTime?: number }> = {
  basic: { monthly: 14900 },
  premium: { monthly: 29900 },
  lifetime: { oneTime: 490000 },
}

export interface UserQuota {
  user_id: string
  plan_type: PlanType
  storage_usage_bytes: number
  generation_count_this_month: number
  quota_reset_at: string
  max_generations: number
  max_storage_bytes: number
  storage_used_gb: number
  storage_limit_gb: number
  generations_remaining: number
  is_over_generation: boolean
  is_over_storage: boolean
}

export async function getUserQuota(userId: string): Promise<UserQuota | null> {
  if (!supabase) return null

  const { data: row, error } = await supabase
    .from('user_quotas')
    .select('*')
    .eq('user_id', userId)
    .single()

  if (error && error.code !== 'PGRST116') {
    console.error('quota 조회 실패:', error)
    return null
  }

  const plan = (row?.plan_type as PlanType) || 'basic'
  const limits = PLAN_LIMITS[plan]
  const storageUsage = Number(row?.storage_usage_bytes ?? 0)
  const genCount = Number(row?.generation_count_this_month ?? 0)
  const maxStorageBytes = limits.storageGB * 1024 * 1024 * 1024

  return {
    user_id: userId,
    plan_type: plan,
    storage_usage_bytes: storageUsage,
    generation_count_this_month: genCount,
    quota_reset_at: row?.quota_reset_at ?? '',
    max_generations: limits.generations,
    max_storage_bytes: maxStorageBytes,
    storage_used_gb: storageUsage / (1024 ** 3),
    storage_limit_gb: limits.storageGB,
    generations_remaining: Math.max(0, limits.generations - genCount),
    is_over_generation: genCount >= limits.generations,
    is_over_storage: storageUsage >= maxStorageBytes,
  }
}

export async function ensureUserQuota(userId: string, planType: PlanType = 'basic'): Promise<void> {
  if (!supabase) return
  await supabase.rpc('ensure_user_quota', {
    p_user_id: userId,
    p_plan_type: planType,
  })
}
