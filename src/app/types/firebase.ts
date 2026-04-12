export interface Device {
  device_id: string
  serial_number: string
  model_name: string
  user_id: string | null
  registered_at: string | null
  wifi_connected: boolean
  last_sync: string | null
  is_playing?: boolean
  playback_progress?: number
}

export interface ContentPermission {
  content_id: string
  user_id: string
  payment_id: string
  payment_status: 'pending' | 'completed' | 'failed'
  amount: number
  nfc_write_allowed: boolean
  nfc_written: boolean
  slot_number: number | null
  video_id: string
  created_at: string
  nfc_written_at: string | null
}

export interface User {
  user_id: string
  email: string
  name: string
  created_at: string
  registered_devices: string[]
}

export interface FinalContent {
  content_id: string
  user_id: string

  main_photo_url: string
  hologram_video_id: string
  /** ZoeDepth 깊이맵 URL - 메인 사진/슬롯 배경 3D 재생용 */
  main_depth_url?: string | null

  background_image_url: string
  background_theme_id: string
  background_theme_name?: string
  /** 배경 이미지 3D용 깊이맵 (추후 BytePlus 생성 배경도 ZoeDepth 적용) */
  background_depth_url?: string | null

  composed_preview_url: string

  mixed_audio_url: string

  payment_id: string
  payment_status: 'pending' | 'completed' | 'failed'
  amount: number

  nfc_slot_number: number | null
  nfc_written: boolean
  nfc_written_at: string | null

  created_at: string
}
