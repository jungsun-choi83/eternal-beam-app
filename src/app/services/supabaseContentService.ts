/**
 * Supabase 콘텐츠·슬롯 서비스
 * - 결제 후 콘텐츠 저장
 * - NFC 슬롯에 쓸 때 슬롯 ↔ 콘텐츠 매핑 저장
 * - 기기: 슬롯 번호로 재생 URL 조회
 */
import { supabase } from '../config/supabase'
import type { SupabaseContent, PlaybackBySlot } from '../types/supabase'

/** 콘텐츠 저장 (결제 완료 후 앱에서 호출) */
export async function saveContentToSupabase(row: {
  content_id: string
  user_id: string
  main_photo_url?: string | null
  hologram_video_id?: string | null
  video_url?: string | null
  background_image_url?: string | null
  background_theme_id?: string | null
  background_theme_name?: string | null
  composed_preview_url?: string | null
  mixed_audio_url?: string | null
  payment_id?: string | null
  payment_status?: string
  amount?: number
  nfc_slot_number?: number | null
  nfc_written?: boolean
  nfc_written_at?: string | null
}): Promise<{ error: Error | null }> {
  if (!supabase) {
    return { error: new Error('Supabase가 설정되지 않았습니다. .env를 확인하세요.') }
  }

  const { error } = await supabase.from('contents').upsert(
    {
      content_id: row.content_id,
      user_id: row.user_id,
      main_photo_url: row.main_photo_url ?? null,
      hologram_video_id: row.hologram_video_id ?? null,
      video_url: row.video_url ?? null,
      background_image_url: row.background_image_url ?? null,
      background_theme_id: row.background_theme_id ?? null,
      background_theme_name: row.background_theme_name ?? null,
      composed_preview_url: row.composed_preview_url ?? null,
      mixed_audio_url: row.mixed_audio_url ?? null,
      payment_id: row.payment_id ?? null,
      payment_status: row.payment_status ?? 'completed',
      amount: row.amount ?? 0,
      nfc_slot_number: row.nfc_slot_number ?? null,
      nfc_written: row.nfc_written ?? false,
      nfc_written_at: row.nfc_written_at ?? null,
    },
    { onConflict: 'content_id' }
  )

  if (error) {
    console.error('Supabase contents 저장 실패:', error)
    return { error }
  }
  return { error: null }
}

/**
 * 슬롯에 콘텐츠 매핑 (NFC 쓰기 시 앱에서 호출)
 * 사용자가 "2번 슬롯에 저장" 하면 slot_number=2, content_id 저장
 */
export async function mapSlotToContent(
  slotNumber: number,
  contentId: string,
  deviceId?: string
): Promise<{ error: Error | null }> {
  if (!supabase) {
    return { error: new Error('Supabase가 설정되지 않았습니다.') }
  }

  const { error } = await supabase.from('slot_content_mapping').upsert(
    {
      slot_number: slotNumber,
      content_id: contentId,
      device_id: deviceId ?? null,
      updated_at: new Date().toISOString(),
    },
    { onConflict: 'slot_number' }
  )

  if (error) {
    console.error('Supabase 슬롯 매핑 실패:', error)
    return { error }
  }
  return { error: null }
}

/**
 * 슬롯 번호로 재생 정보 조회 (기기 또는 앱에서 호출)
 * 기기가 NFC로 "2번 슬롯" 읽으면 → video_url, audio_url 받아서 재생
 */
export async function getPlaybackBySlot(
  slotNumber: number
): Promise<{ data: PlaybackBySlot | null; error: Error | null }> {
  if (!supabase) {
    return { data: null, error: new Error('Supabase가 설정되지 않았습니다.') }
  }

  const { data, error } = await supabase.rpc('get_playback_by_slot', {
    p_slot_number: slotNumber,
  })

  if (error) {
    console.error('get_playback_by_slot 실패:', error)
    return { data: null, error }
  }

  const row = Array.isArray(data) ? data[0] : data
  return { data: row as PlaybackBySlot | null, error: null }
}

/**
 * 기기 슬롯 인식 이벤트 로그 (선택, 재생 이력 확인용)
 */
export async function logDeviceSlotEvent(
  slotNumber: number,
  contentId: string | null,
  deviceId?: string
): Promise<{ error: Error | null }> {
  if (!supabase) return { error: null }

  const { error } = await supabase.from('device_slot_events').insert({
    slot_number: slotNumber,
    content_id: contentId,
    device_id: deviceId ?? null,
  })

  if (error) console.error('device_slot_events 로그 실패:', error)
  return { error: error ? new Error(error.message) : null }
}
