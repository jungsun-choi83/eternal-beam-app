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

  background_image_url: string
  background_theme_id: string
  background_theme_name?: string

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
