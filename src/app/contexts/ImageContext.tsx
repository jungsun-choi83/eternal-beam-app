import { createContext, useContext, useState, type ReactNode } from 'react'

export type MediaType = 'image' | 'video'

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
}

const ImageContext = createContext<ImageContextType | undefined>(undefined)

export function ImageProvider({ children }: { children: ReactNode }) {
  const [selectedImage, setSelectedImage] = useState<string | null>(null)
  const [mediaType, setMediaType] = useState<MediaType>('image')
  const [uploadedFile, setUploadedFile] = useState<File | null>(null)
  const [selectedTheme, setSelectedTheme] = useState('christmas')
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null)
  const [depthMap, setDepthMap] = useState<string | null>(null)

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
