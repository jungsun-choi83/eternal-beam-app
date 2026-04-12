import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

export type MediaType = 'image' | 'video'

export interface PreviewControls {
  scale: number
  positionX: number
  positionY: number
}

const DEFAULT_PREVIEW_CONTROLS: PreviewControls = {
  scale: 1.0,
  positionX: 0,
  positionY: 0,
}

interface ImageContextType {
  selectedImage: string | null
  setSelectedImage: (image: string | null) => void
  mediaType: MediaType
  setMediaType: (t: MediaType) => void
  uploadedFile: File | null
  setUploadedFile: (file: File | null) => void
  selectedTheme: string
  setSelectedTheme: (theme: string) => void
  audioBlob: Blob | null
  setAudioBlob: (blob: Blob | null) => void
  depthMap: string | null
  setDepthMap: (depthMap: string | null) => void
  contentId: string | null
  setContentId: (id: string | null) => void
  previewControls: PreviewControls
  setPreviewControls: (c: PreviewControls | ((prev: PreviewControls) => PreviewControls)) => void
  resetPreviewControls: () => void
}

const ImageContext = createContext<ImageContextType | undefined>(undefined)

export function ImageProvider({ children }: { children: ReactNode }) {
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaType>('image')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [selectedTheme, setSelectedTheme] = useState('christmas')
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null)
  const [depthMap, setDepthMap] = useState<string | null>(null)
  const [contentId, setContentIdState] = useState<string | null>(() =>
    typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_content_id') : null
  )

  const setContentId = useCallback((id: string | null) => {
    setContentIdState(id)
    try {
      if (id) localStorage.setItem('eternal_beam_content_id', id)
      else localStorage.removeItem('eternal_beam_content_id')
    } catch {}
  }, [])
  const [previewControls, setPreviewControlsState] = useState<PreviewControls>(() => {
    try {
      const raw = typeof localStorage !== 'undefined' ? localStorage.getItem('eternal_beam_preview_controls') : null
      if (raw) {
        const parsed = JSON.parse(raw) as PreviewControls
        return { ...DEFAULT_PREVIEW_CONTROLS, ...parsed }
      }
    } catch {}
    return DEFAULT_PREVIEW_CONTROLS
  })

  const setPreviewControls = useCallback((c: PreviewControls | ((prev: PreviewControls) => PreviewControls)) => {
    setPreviewControlsState((prev) => {
      const next = typeof c === 'function' ? c(prev) : c
      try {
        localStorage.setItem('eternal_beam_preview_controls', JSON.stringify(next))
      } catch {}
      return next
    })
  }, [])

  const resetPreviewControls = useCallback(() => {
    setPreviewControlsState(DEFAULT_PREVIEW_CONTROLS)
    try {
      localStorage.setItem('eternal_beam_preview_controls', JSON.stringify(DEFAULT_PREVIEW_CONTROLS))
    } catch {}
  }, [])

  return (
    <ImageContext.Provider
      value={{
        selectedImage,
        setSelectedImage,
        mediaType,
        setMediaType,
        uploadedFile,
        setUploadedFile,
        selectedTheme,
        setSelectedTheme,
        audioBlob,
        setAudioBlob,
        depthMap,
        setDepthMap,
        contentId,
        setContentId,
        previewControls,
        setPreviewControls,
        resetPreviewControls,
      }}
    >
      {children}
    </ImageContext.Provider>
  )
}

export function useImage() {
  const context = useContext(ImageContext)
  if (!context) {
    throw new Error('useImage must be used within ImageProvider')
  }
  return context
}
