/** Supabase contents 테이블 한 행 */
export interface SupabaseContent {
  content_id: string
  user_id: string
  main_photo_url: string | null
  hologram_video_id: string | null
  video_url: string | null
  background_image_url: string | null
  background_theme_id: string | null
  background_theme_name: string | null
  composed_preview_url: string | null
  mixed_audio_url: string | null
  payment_id: string | null
  payment_status: string | null
  amount: number | null
  nfc_slot_number: number | null
  nfc_written: boolean | null
  nfc_written_at: string | null
  created_at: string | null
}

/** 슬롯 번호로 재생 정보 조회 결과 (get_playback_by_slot RPC) */
export interface PlaybackBySlot {
  content_id: string
  video_url: string | null
  mixed_audio_url: string | null
  composed_preview_url: string | null
  background_theme_name: string | null
}
