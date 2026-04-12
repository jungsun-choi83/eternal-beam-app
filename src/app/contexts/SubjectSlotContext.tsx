import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'
import { BGM_PRESETS, type BGMPreset } from '../services/audioMixer'

export interface ThemeSlot {
  id: string
  name: string
  nameKo: string
  gradient: string
  isPremium: boolean
  price: number
  bgmUrl?: string
}

const THEME_SLOTS: ThemeSlot[] = BGM_PRESETS.map((p) => ({
  id: p.id,
  name: p.name,
  nameKo:
    p.id === 'sweet_memory'
      ? '달빛'
      : p.id === 'galaxy_dream'
        ? '은하수'
        : p.id === 'nature_bloom'
          ? '숲'
          : p.id === 'ocean_wave'
            ? '바다'
            : p.id === 'neon_city'
              ? '네온'
              : p.id === 'golden_hour'
                ? '무지개'
                : p.name,
  gradient: p.gradient,
  isPremium: p.price > 0,
  price: p.price,
  bgmUrl: p.bgmUrl,
}))

interface SubjectSlotContextType {
  subjectImageUrl: string | null
  setSubjectImageUrl: (url: string | null) => void
  selectedThemeId: string | null
  setSelectedThemeId: (id: string | null) => void
  themes: ThemeSlot[]
  paidThemeIds: Set<string>
  setThemePaid: (themeId: string) => void
  canUseTheme: (themeId: string) => boolean
  getTheme: (id: string) => ThemeSlot | undefined
}

const SubjectSlotContext = createContext<SubjectSlotContextType | undefined>(undefined)

export function SubjectSlotProvider({ children }: { children: ReactNode }) {
  const [subjectImageUrl, setSubjectImageUrl] = useState<string | null>(() =>
    typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_main_photo') : null,
  )
  const [selectedThemeId, setSelectedThemeId] = useState<string | null>(() =>
    typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_background_theme_id') : null,
  )
  const [paidThemeIds, setPaidThemeIds] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem('eternal_beam_paid_theme_ids')
      if (raw) {
        const arr = JSON.parse(raw) as string[]
        return new Set(arr)
      }
    } catch {}
    return new Set()
  })

  const setThemePaid = useCallback((themeId: string) => {
    setPaidThemeIds((prev) => {
      const next = new Set(prev)
      next.add(themeId)
      try {
        localStorage.setItem('eternal_beam_paid_theme_ids', JSON.stringify([...next]))
      } catch {}
      return next
    })
  }, [])

  const canUseTheme = useCallback(
    (themeId: string) => {
      const theme = THEME_SLOTS.find((t) => t.id === themeId)
      if (!theme) return false
      if (!theme.isPremium) return true
      return paidThemeIds.has(themeId)
    },
    [paidThemeIds],
  )

  const getTheme = useCallback((id: string) => THEME_SLOTS.find((t) => t.id === id), [])

  return (
    <SubjectSlotContext.Provider
      value={{
        subjectImageUrl,
        setSubjectImageUrl,
        selectedThemeId,
        setSelectedThemeId,
        themes: THEME_SLOTS,
        paidThemeIds,
        setThemePaid,
        canUseTheme,
        getTheme,
      }}
    >
      {children}
    </SubjectSlotContext.Provider>
  )
}

export function useSubjectSlot() {
  const ctx = useContext(SubjectSlotContext)
  if (!ctx) throw new Error('useSubjectSlot must be used within SubjectSlotProvider')
  return ctx
}

export { THEME_SLOTS }
