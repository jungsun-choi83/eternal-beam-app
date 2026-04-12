/**
 * 기기 슬롯 → 재생 URL 조회 (라즈베리 파이용)
 * - slot_content_mapping + contents 조인하여 슬롯별 video_url 반환
 * - env는 호출 시점에 읽음 (dotenv가 먼저 로드되도록)
 */

import { createClient } from '@supabase/supabase-js'

function getSupabase() {
  const supabaseUrl = process.env.SUPABASE_URL || process.env.VITE_SUPABASE_URL || ''
  const supabaseServiceKey = process.env.SUPABASE_SERVICE_ROLE_KEY || ''
  if (!supabaseUrl || !supabaseServiceKey) return null
  return createClient(supabaseUrl, supabaseServiceKey)
}

const SLOT_MIN = 1
const SLOT_MAX = 5

/**
 * 기기에서 재생할 슬롯별 영상 URL 조회
 * @param {string} deviceId - 기기 ID (현재 스키마는 전역 슬롯 1~5만 사용, deviceId는 로깅/확장용)
 * @returns {Promise<{ slots: Record<string, string>, error: Error | null }>}
 *   slots: { "1": "https://...", "2": "https://...", ... } (없는 슬롯은 키 없음)
 */
export async function getSlotsForDevice(deviceId) {
  const supabase = getSupabase()
  if (!supabase) {
    return {
      slots: {},
      error: new Error('Supabase가 설정되지 않았습니다. SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY를 확인하세요.'),
    }
  }

  try {
    const { data: mappings, error: mapError } = await supabase
      .from('slot_content_mapping')
      .select('slot_number, content_id')
      .gte('slot_number', SLOT_MIN)
      .lte('slot_number', SLOT_MAX)

    if (mapError) {
      console.error('[deviceSlots] slot_content_mapping 조회 실패:', mapError)
      return { slots: {}, error: mapError }
    }

    if (!mappings?.length) {
      return { slots: {}, error: null }
    }

    const contentIds = [...new Set(mappings.map((m) => m.content_id))]
    const { data: contents, error: contentError } = await supabase
      .from('contents')
      .select('content_id, video_url, composed_preview_url')
      .in('content_id', contentIds)

    if (contentError) {
      console.error('[deviceSlots] contents 조회 실패:', contentError)
      return { slots: {}, error: contentError }
    }

    const urlByContentId = new Map()
    for (const c of contents || []) {
      const url = c.video_url || c.composed_preview_url || null
      if (url) urlByContentId.set(c.content_id, url)
    }

    const slots = {}
    for (const m of mappings) {
      const url = urlByContentId.get(m.content_id)
      if (url) slots[String(m.slot_number)] = url
    }

    return { slots, error: null }
  } catch (err) {
    console.error('[deviceSlots] getSlotsForDevice 예외:', err)
    return { slots: {}, error: err }
  }
}
