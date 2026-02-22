import { ArrowRight, Upload, Image as ImageIcon, Camera } from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { useState, useRef, useEffect } from 'react'
import { getMediaType } from '../../utils/mediaType'

const ACCEPT_MEDIA = 'image/*,video/mp4,video/webm,video/quicktime'

interface PhotoUploadScreenProps {
  onNavigate?: (screen: string) => void
}

export function PhotoUploadScreen({ onNavigate }: PhotoUploadScreenProps) {
  const { t } = useLanguage()
  const { selectedImage, setSelectedImage, setMediaType } = useImage()
  const [isDragging, setIsDragging] = useState(false)
  const selectedMedia = selectedImage
  const fileInputRef = useRef<HTMLInputElement>(null)
  const videoUrlRef = useRef<string | null>(null)

  const editPhotoOnly = localStorage.getItem('eternal_beam_edit_photo_only') === '1'

  useEffect(() => {
    return () => {
      if (videoUrlRef.current) URL.revokeObjectURL(videoUrlRef.current)
    }
  }, [])

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
        localStorage.setItem('eternal_beam_main_photo', result)
        localStorage.removeItem('eternal_beam_main_video_url')
      }
      reader.readAsDataURL(file)
    } else {
      if (videoUrlRef.current) URL.revokeObjectURL(videoUrlRef.current)
      const url = URL.createObjectURL(file)
      videoUrlRef.current = url
      setSelectedImage(url)
      localStorage.setItem('eternal_beam_main_video_url', url)
      localStorage.removeItem('eternal_beam_main_photo')
    }
  }

  const handleConvert = () => {
    if (!selectedMedia) {
      alert('사진 또는 동영상을 먼저 선택해주세요.')
      return
    }
    if (editPhotoOnly) {
      localStorage.removeItem('eternal_beam_edit_photo_only')
      onNavigate?.('preview')
      return
    }
    const afterUpload = localStorage.getItem('eternal_beam_after_upload')
    if (afterUpload === 'themeSelection') {
      localStorage.removeItem('eternal_beam_after_upload')
      onNavigate?.('themeSelection')
      return
    }
    const videoId =
      'holo_' +
      Date.now() +
      '_' +
      Math.random().toString(36).substring(2, 9)
    localStorage.setItem('eternal_beam_hologram_video_id', videoId)
    localStorage.setItem('eternal_beam_current_video_id', videoId)
    onNavigate?.('aiProcessing')
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
            {editPhotoOnly ? '사진/동영상 변경' : '메인 사진·동영상 선택'}
          </h1>
          <button
            onClick={() => {
              localStorage.removeItem('eternal_beam_edit_photo_only')
              onNavigate?.(editPhotoOnly ? 'preview' : 'home')
            }}
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          {editPhotoOnly ? '새로운 사진 또는 동영상으로 교체하세요' : '홀로그램으로 변환할 사진 또는 동영상을 선택하세요'}
        </p>
      </div>

      {/* Preview Area */}
      {selectedMedia && (
        <div className="px-6 pb-4">
          <div
            className="glass overflow-hidden rounded-2xl"
            style={{
              aspectRatio: '1',
              maxHeight: '280px',
            }}
          >
            {localStorage.getItem('eternal_beam_media_type') === 'video' ? (
              <video
                src={selectedMedia}
                className="h-full w-full object-cover"
                controls
                loop
                muted
                playsInline
              />
            ) : (
              <img
                src={selectedMedia}
                alt="Selected"
                className="h-full w-full object-cover"
              />
            )}
          </div>
        </div>
      )}

      {/* Upload Area */}
      <div className="px-6 py-4">
        <div
            className={`glass flex min-h-[200px] flex-col items-center justify-center rounded-3xl border-2 border-dashed p-8 transition-all ${isDragging ? 'border-[#9B7EBD]' : 'border-white/40'}`}
          onDragOver={(e) => {
            e.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(e) => {
            e.preventDefault()
            setIsDragging(false)
            const file = e.dataTransfer.files[0]
            if (file) handleFileSelect(file)
          }}
        >
          <Upload className="mb-4 h-12 w-12 text-[#9B7EBD]" />
          <p className="mb-2 text-sm font-semibold" style={{ color: '#2D2640' }}>
            {t('upload.drag')}
          </p>
          <p className="mb-4 text-xs" style={{ color: '#718096' }}>
            JPG, PNG / MP4, WebM (이미지 10MB, 동영상 100MB)
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT_MEDIA}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) handleFileSelect(file)
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            className="glass rounded-xl px-6 py-2 text-sm font-semibold text-[#9B7EBD]"
          >
            <ImageIcon className="mr-2 inline h-4 w-4" />
            갤러리에서 선택
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
            className="glass mt-2 flex items-center gap-2 rounded-xl px-6 py-2 text-sm font-semibold text-[#9B7EBD]"
          >
            <Camera className="h-4 w-4" />
            카메라로 촬영
          </button>
        </div>
      </div>

      {/* Convert Button */}
      <div className="px-6 pb-8">
        <button
          onClick={handleConvert}
          disabled={!selectedMedia}
          className="glass-strong flex w-full items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white transition-all disabled:opacity-50"
          style={{
            background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
            boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
          }}
        >
          {editPhotoOnly
            ? '변경 완료'
            : localStorage.getItem('eternal_beam_after_upload') === 'themeSelection'
              ? '테마 선택으로'
              : '3D 홀로그램으로 변환'}
        </button>
      </div>
    </div>
  )
}
