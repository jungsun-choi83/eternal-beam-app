import { ArrowRight, Upload, Image as ImageIcon, Camera, Sparkles } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { useState, useRef } from 'react'
import { getMediaType } from '../../utils/mediaType'

const ACCEPT_MEDIA = 'image/*,video/mp4,video/webm,video/quicktime'

interface UploadScreenProps {
  onNavigate?: (screen: string) => void
}

export function UploadScreen({ onNavigate }: UploadScreenProps) {
  const { t } = useLanguage()
  const { setSelectedImage, setUploadedFile, setMediaType } = useImage()
  const [isDragging, setIsDragging] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFileSelect = (file: File) => {
    if (!file) return
    const isImage = file.type.startsWith('image/')
    const isVideo = file.type.startsWith('video/')
    if (!isImage && !isVideo) {
      alert('이미지(JPG, PNG) 또는 동영상(MP4, WebM) 파일만 업로드 가능합니다.')
      return
    }
    if (isVideo && file.size > 100 * 1024 * 1024) {
      alert('동영상은 100MB 이하로 선택해주세요.')
      return
    }

    const type = getMediaType(file)
    setMediaType(type)
    localStorage.setItem('eternal_beam_media_type', type)

    if (isImage) {
      const reader = new FileReader()
      reader.onload = (e) => {
        const result = e.target?.result as string
        setSelectedImage(result)
        setUploadedFile(file)
        localStorage.setItem('eternal_beam_main_photo', result)
        localStorage.removeItem('eternal_beam_main_video_url')
        onNavigate?.('aiprocess')
      }
      reader.readAsDataURL(file)
    } else {
      const url = URL.createObjectURL(file)
      setSelectedImage(url)
      setUploadedFile(file)
      localStorage.setItem('eternal_beam_main_video_url', url)
      localStorage.removeItem('eternal_beam_main_photo')
      onNavigate?.('themeSelection')
    }
  }

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      {/* Header */}
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1
            className="font-headline text-2xl font-bold"
            style={{ color: '#2D2640' }}
          >
            {t('upload.title')}
          </h1>
          <button
            onClick={() => onNavigate?.('home')}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          {t('upload.desc')}
        </p>
      </div>

      {/* Main Upload Area */}
      <div className="px-6 py-6">
        <div
          className={`glass flex min-h-[300px] flex-col items-center justify-center rounded-3xl border-2 border-dashed p-8 transition-all ${isDragging ? 'border-[#9B7EBD]' : 'border-white/40'}`}
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setIsDragging(false)
            const file = e.dataTransfer.files[0]
            if (file) {
              handleFileSelect(file)
            }
          }}
        >
          <div className="glass mb-6 flex h-24 w-24 items-center justify-center rounded-full">
            <Upload className="h-12 w-12 text-[#9B7EBD]" />
          </div>

          <h3 className="mb-2 text-lg font-bold" style={{ color: '#2D2640' }}>
            {t('upload.drag')}
          </h3>
          <p className="mb-6 text-sm" style={{ color: '#718096' }}>
            JPG, PNG, HEIC / MP4, WebM (이미지 10MB, 동영상 100MB)
          </p>
        </div>
      </div>

      {/* Upload Options */}
      <div className="space-y-3 px-6 py-4">
        <button
          onClick={() => fileInputRef.current?.click()}
          className="glass-strong flex w-full items-center justify-between rounded-2xl px-6 py-4 transition-all text-white"
          style={{
            background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
          }}
        >
          <div className="flex items-center gap-3">
            <ImageIcon className="h-6 w-6" />
            <div className="text-left">
              <div className="font-bold">{t('upload.device')}</div>
              <div className="text-xs opacity-90">사진·동영상 라이브러리</div>
            </div>
          </div>
          <Sparkles className="h-5 w-5" />
        </button>

        <button
          onClick={() => {
            const input = document.createElement('input')
            input.type = 'file'
            input.accept = ACCEPT_MEDIA
            input.setAttribute('capture', 'environment')
            input.onchange = (e) => {
              const file = (e.target as HTMLInputElement).files?.[0]
              if (file) handleFileSelect(file)
            }
            input.click()
          }}
          className="glass flex w-full items-center justify-between rounded-2xl px-6 py-4 transition-all"
          style={{ color: '#2D2640' }}
        >
          <div className="flex items-center gap-3">
            <Camera className="h-6 w-6 text-[#9B7EBD]" />
            <div className="text-left">
              <div className="font-bold">{t('upload.camera')}</div>
              <div className="text-xs" style={{ color: '#718096' }}>
                지금 바로 촬영
              </div>
            </div>
          </div>
        </button>

        <button
          onClick={() => onNavigate?.('gallery')}
          className="glass flex w-full items-center justify-between rounded-2xl px-6 py-4 transition-all"
          style={{ color: '#2D2640' }}
        >
          <div className="flex items-center gap-3">
            <ImageIcon className="h-6 w-6 text-[#9B7EBD]" />
            <div className="text-left">
              <div className="font-bold">{t('upload.gallery')}</div>
              <div className="text-xs" style={{ color: '#718096' }}>
                기존 홀로그램 선택
              </div>
            </div>
          </div>
        </button>
      </div>

      {/* Hidden File Input */}
      <input
        ref={fileInputRef}
        type="file"
        accept={ACCEPT_MEDIA}
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) {
            handleFileSelect(file)
          }
        }}
      />
    </div>
  )
}
