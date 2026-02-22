/**
 * LocalStorage 유틸리티
 * 데이터 영구 저장을 위한 헬퍼 함수들
 */

// Storage Keys
export const STORAGE_KEYS = {
  USER: 'eternal_beam_user',
  PURCHASED_THEMES: 'eternal_beam_purchased_themes',
  UPLOADED_IMAGES: 'eternal_beam_images',
  VOICE_RECORDINGS: 'eternal_beam_recordings',
  NFC_SLOTS: 'eternal_beam_nfc_slots',
  SETTINGS: 'eternal_beam_settings',
  PAYMENT_HISTORY: 'eternal_beam_payments',
} as const

// Type-safe storage operations
export const storage = {
  get: <T>(key: string): T | null => {
    try {
      const item = localStorage.getItem(key)
      return item ? JSON.parse(item) : null
    } catch (error) {
      console.error(`Error getting ${key} from localStorage:`, error)
      return null
    }
  },
  set: <T>(key: string, value: T): boolean => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
      return true
    } catch (error) {
      console.error(`Error setting ${key} in localStorage:`, error)
      return false
    }
  },
  remove: (key: string): boolean => {
    try {
      localStorage.removeItem(key)
      return true
    } catch (error) {
      console.error(`Error removing ${key} from localStorage:`, error)
      return false
    }
  },
  clear: (): boolean => {
    try {
      localStorage.clear()
      return true
    } catch (error) {
      console.error('Error clearing localStorage:', error)
      return false
    }
  },
}

// User Data Types
export interface UserData {
  id: string
  name: string
  email: string
  createdAt: string
  lastLogin: string
}

export interface PurchasedTheme {
  themeId: string
  themeName: string
  price: number
  purchasedAt: string
  orderId: string
}

export interface UploadedImage {
  id: string
  dataUrl: string
  fileName: string
  uploadedAt: string
  depthMap?: string
}

export interface VoiceRecording {
  id: string
  audioBlob: string
  duration: number
  recordedAt: string
  themeName?: string
}

export interface NFCSlot {
  id: number
  number: string
  theme: string | null
  emoji: string | null
  assigned: boolean
  assignedAt?: string
}

export interface AppSettings {
  language: 'ko' | 'en' | 'zh'
  notifications: boolean
  autoSync: boolean
  theme: 'light' | 'dark' | 'auto'
}

export interface PaymentHistory {
  orderId: string
  items: Array<{ name: string; price: number; quantity: number }>
  totalAmount: number
  paymentMethod: string
  status: 'pending' | 'completed' | 'failed'
  createdAt: string
}

// User operations
export const userStorage = {
  get: () => storage.get<UserData>(STORAGE_KEYS.USER),
  set: (user: UserData) => storage.set(STORAGE_KEYS.USER, user),
  remove: () => storage.remove(STORAGE_KEYS.USER),
  updateLastLogin: () => {
    const user = userStorage.get()
    if (user) {
      user.lastLogin = new Date().toISOString()
      userStorage.set(user)
    }
  },
}

// Purchased themes operations
export const themesStorage = {
  get: () => storage.get<PurchasedTheme[]>(STORAGE_KEYS.PURCHASED_THEMES) || [],
  add: (theme: PurchasedTheme) => {
    const themes = themesStorage.get()
    themes.push(theme)
    return storage.set(STORAGE_KEYS.PURCHASED_THEMES, themes)
  },
  has: (themeId: string) => {
    const themes = themesStorage.get()
    return themes.some((t) => t.themeId === themeId)
  },
  getIds: () => {
    const themes = themesStorage.get()
    return themes.map((t) => t.themeId)
  },
}

// Images operations
export const imagesStorage = {
  get: () => storage.get<UploadedImage[]>(STORAGE_KEYS.UPLOADED_IMAGES) || [],
  add: (image: UploadedImage) => {
    const images = imagesStorage.get()
    images.push(image)
    return storage.set(STORAGE_KEYS.UPLOADED_IMAGES, images)
  },
  getLatest: () => {
    const images = imagesStorage.get()
    return images.length > 0 ? images[images.length - 1] : null
  },
  remove: (id: string) => {
    const images = imagesStorage.get().filter((img) => img.id !== id)
    return storage.set(STORAGE_KEYS.UPLOADED_IMAGES, images)
  },
}

// Voice recordings operations
export const recordingsStorage = {
  get: () =>
    storage.get<VoiceRecording[]>(STORAGE_KEYS.VOICE_RECORDINGS) || [],
  add: (recording: VoiceRecording) => {
    const recordings = recordingsStorage.get()
    recordings.push(recording)
    return storage.set(STORAGE_KEYS.VOICE_RECORDINGS, recordings)
  },
  getLatest: () => {
    const recordings = recordingsStorage.get()
    return recordings.length > 0 ? recordings[recordings.length - 1] : null
  },
  remove: (id: string) => {
    const recordings = recordingsStorage
      .get()
      .filter((rec) => rec.id !== id)
    return storage.set(STORAGE_KEYS.VOICE_RECORDINGS, recordings)
  },
}

// NFC slots operations
export const nfcSlotsStorage = {
  get: () => storage.get<NFCSlot[]>(STORAGE_KEYS.NFC_SLOTS) || [],
  set: (slots: NFCSlot[]) => storage.set(STORAGE_KEYS.NFC_SLOTS, slots),
  assign: (slotId: number, theme: string, emoji: string) => {
    const slots = nfcSlotsStorage.get()
    const slot = slots.find((s) => s.id === slotId)
    if (slot) {
      slot.theme = theme
      slot.emoji = emoji
      slot.assigned = true
      slot.assignedAt = new Date().toISOString()
      return nfcSlotsStorage.set(slots)
    }
    return false
  },
  clear: (slotId: number) => {
    const slots = nfcSlotsStorage.get()
    const slot = slots.find((s) => s.id === slotId)
    if (slot) {
      slot.theme = null
      slot.emoji = null
      slot.assigned = false
      slot.assignedAt = undefined
      return nfcSlotsStorage.set(slots)
    }
    return false
  },
}

// Settings operations
export const settingsStorage = {
  get: () =>
    storage.get<AppSettings>(STORAGE_KEYS.SETTINGS) || {
      language: 'ko',
      notifications: true,
      autoSync: true,
      theme: 'auto',
    },
  set: (settings: AppSettings) => storage.set(STORAGE_KEYS.SETTINGS, settings),
  update: (partial: Partial<AppSettings>) => {
    const settings = settingsStorage.get()
    return settingsStorage.set({ ...settings, ...partial })
  },
}

// Payment history operations
export const paymentsStorage = {
  get: () =>
    storage.get<PaymentHistory[]>(STORAGE_KEYS.PAYMENT_HISTORY) || [],
  add: (payment: PaymentHistory) => {
    const payments = paymentsStorage.get()
    payments.push(payment)
    return storage.set(STORAGE_KEYS.PAYMENT_HISTORY, payments)
  },
  getByOrderId: (orderId: string) => {
    const payments = paymentsStorage.get()
    return payments.find((p) => p.orderId === orderId)
  },
  updateStatus: (orderId: string, status: PaymentHistory['status']) => {
    const payments = paymentsStorage.get()
    const payment = payments.find((p) => p.orderId === orderId)
    if (payment) {
      payment.status = status
      return storage.set(STORAGE_KEYS.PAYMENT_HISTORY, payments)
    }
    return false
  },
}

// Initialize default data
export const initializeStorage = () => {
  if (!storage.get(STORAGE_KEYS.SETTINGS)) {
    settingsStorage.set({
      language: 'ko',
      notifications: true,
      autoSync: true,
      theme: 'auto',
    })
  }
  if (!storage.get(STORAGE_KEYS.PURCHASED_THEMES)) {
    storage.set(STORAGE_KEYS.PURCHASED_THEMES, [])
  }
  if (!storage.get(STORAGE_KEYS.UPLOADED_IMAGES)) {
    storage.set(STORAGE_KEYS.UPLOADED_IMAGES, [])
  }
  if (!storage.get(STORAGE_KEYS.VOICE_RECORDINGS)) {
    storage.set(STORAGE_KEYS.VOICE_RECORDINGS, [])
  }
  if (!storage.get(STORAGE_KEYS.PAYMENT_HISTORY)) {
    storage.set(STORAGE_KEYS.PAYMENT_HISTORY, [])
  }
  if (!storage.get(STORAGE_KEYS.NFC_SLOTS)) {
    storage.set(STORAGE_KEYS.NFC_SLOTS, [])
  }
  console.log('✅ LocalStorage initialized')
}

export const blobToBase64 = (blob: Blob): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onloadend = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

export const base64ToBlob = (
  base64: string,
  type = 'audio/webm',
): Blob => {
  const byteString = atob(base64.split(',')[1])
  const mimeString = base64.split(',')[0].split(':')[1].split(';')[0]
  const ab = new ArrayBuffer(byteString.length)
  const ia = new Uint8Array(ab)
  for (let i = 0; i < byteString.length; i++) {
    ia[i] = byteString.charCodeAt(i)
  }
  return new Blob([ab], { type: mimeString || type })
}
