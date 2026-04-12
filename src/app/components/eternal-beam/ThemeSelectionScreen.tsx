import {
  useState,
  useRef,
  useEffect,
} from 'react'
import {
  ArrowRight,
  Upload,
  Check,
  Mic,
  Play,
  Volume2,
  ChevronRight,
  Square,
  Lock,
} from 'lucide-react'
import { useSubjectSlot } from '../../contexts/SubjectSlotContext'
import { composeImageLayers } from '../../utils/imageComposer'
import {
  mixAudioFiles,
  previewMixedAudio,
  BGM_PRESETS,
  type BGMPreset,
} from '../../services/audioMixer'
import { isVideoUrl } from '../../utils/mediaType'

interface ThemeSelectionScreenProps {
  onNavigate?: (screen: string) => void
  onBack?: () => void
}

function gradientToDataUrl(gradient: string, size = 512): string {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')
  if (!ctx) return ''

  const angle = (135 * Math.PI) / 180
  const x1 = size / 2 - (size / 2) * Math.cos(angle)
  const y1 = size / 2 - (size / 2) * Math.sin(angle)
  const x2 = size / 2 + (size / 2) * Math.cos(angle)
  const y2 = size / 2 + (size / 2) * Math.sin(angle)

  const grad = ctx.createLinearGradient(x1, y1, x2, y2)
  const colorMatch = gradient.match(/#[a-fA-F0-9]{3,8}|rgb\([^)]+\)|rgba\([^)]+\)/g)
  if (colorMatch && colorMatch.length >= 2) {
    colorMatch.forEach((color, i) => {
      grad.addColorStop(i / (colorMatch.length - 1), color.trim())
    })
  } else {
    grad.addColorStop(0, '#667eea')
    grad.addColorStop(1, '#764ba2')
  }

  ctx.fillStyle = grad
  ctx.fillRect(0, 0, size, size)
  return canvas.toDataURL('image/jpeg', 0.9)
}

export function ThemeSelectionScreen({
  onNavigate,
  onBack,
}: ThemeSelectionScreenProps) {
  const { canUseTheme } = useSubjectSlot()
  const [backgroundImage, setBackgroundImage] = useState<string | null>(null)
  const [selectedTheme, setSelectedTheme] = useState<BGMPreset | null>(null)
  const [composedPreview, setComposedPreview] = useState<string | null>(null)
  const [isComposing, setIsComposing] = useState(false)
  const [voiceRecording, setVoiceRecording] = useState<Blob | null>(null)
  const [mixedAudio, setMixedAudio] = useState<Blob | null>(null)
  const [isRecording, setIsRecording] = useState(false)
  const [recordingTime, setRecordingTime] = useState(0)
  const [showVoiceUI, setShowVoiceUI] = useState(false)
  const [isMixing, setIsMixing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const editThemeOnly = localStorage.getItem('eternal_beam_edit_theme_only') === '1'

  useEffect(() => {
    const savedBg = localStorage.getItem('eternal_beam_background_image')
    if (savedBg && !backgroundImage) setBackgroundImage(savedBg)
  }, [])

  useEffect(() => {
    if (editThemeOnly) return
    const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
    const mainVideo = localStorage.getItem('eternal_beam_main_video_url')
    if (!mainPhoto && !mainVideo) {
      localStorage.setItem('eternal_beam_after_upload', 'themeSelection')
      onNavigate?.('photoUpload')
    }
  }, [onNavigate, editThemeOnly])

  const handleComposeLayers = async () => {
    // 메인 = 누끼(배경 제거된 이미지) 우선, 없으면 원본 (플로우: AI 누끼 → 테마 위 합성)
    const cutoutUrl = localStorage.getItem('eternal_beam_cutout_url')
    const cutoutBase64 = localStorage.getItem('eternal_beam_cutout_base64')
    const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
    const mainImage = cutoutUrl || (cutoutBase64 ? `data:image/png;base64,${cutoutBase64}` : null) || mainPhoto

    if (!backgroundImage || !mainImage || mainImage.includes('blob:')) return

    setIsComposing(true)
    try {
      const result = await composeImageLayers({
        backgroundImage,
        mainImage,
        mainImageScale: 0.6,
        hologramEffect: true,
        overlayOpacity: 0.2,
        useCutout: !!(cutoutUrl || cutoutBase64), // 누끼일 때 투명 배경 유지
        // 테마 그라데이션일 때 배경 틴트 완화 (직접 업로드 시 1로 전체 노출)
        backgroundOpacity: selectedTheme ? 0.78 : 1,
      })
      setComposedPreview(result.dataUrl)
      localStorage.setItem('eternal_beam_composed_preview', result.dataUrl)
    } catch (e) {
      console.error('합성 실패:', e)
    } finally {
      setIsComposing(false)
    }
  }

  const mainMedia = localStorage.getItem('eternal_beam_main_photo') || localStorage.getItem('eternal_beam_main_video_url')
  const isVideoContent = mainMedia ? isVideoUrl(mainMedia) : false

  // 누끼 결과 (배경 제거된 메인 피사체) — 배경 선택 전 먼저 보여주기용
  const cutoutUrl = localStorage.getItem('eternal_beam_cutout_url')
  const cutoutBase64 = localStorage.getItem('eternal_beam_cutout_base64')
  const cutoutImage = cutoutUrl || (cutoutBase64 ? `data:image/png;base64,${cutoutBase64}` : null)

  useEffect(() => {
    if (isVideoContent && mainMedia) {
      setComposedPreview(mainMedia)
      localStorage.setItem('eternal_beam_composed_preview', mainMedia)
      return
    }
    if (backgroundImage && !isVideoContent) {
      handleComposeLayers()
    } else {
      setComposedPreview(null)
    }
  }, [backgroundImage, isVideoContent, mainMedia, selectedTheme])

  const handleThemeSelect = (theme: BGMPreset) => {
    const isPremium = theme.price > 0
    const owned = canUseTheme(theme.id)
    if (isPremium && !owned) {
      localStorage.setItem('eternal_beam_pending_theme_id', theme.id)
      onNavigate?.('checkout')
      return
    }
    setSelectedTheme(theme)
    const dataUrl = gradientToDataUrl(theme.gradient)
    setBackgroundImage(dataUrl)
    localStorage.setItem('eternal_beam_background_image', dataUrl)
    localStorage.setItem('eternal_beam_background_theme_id', theme.id)
    localStorage.setItem('eternal_beam_background_theme_name', theme.name)
    if (theme.bgmUrl) {
      localStorage.setItem('eternal_beam_bgm_url', theme.bgmUrl)
    }
  }

  const handleBackgroundUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file?.type.startsWith('image/')) return

    const reader = new FileReader()
    reader.onload = () => {
      const url = reader.result as string
      setBackgroundImage(url)
      setSelectedTheme(null)
      localStorage.setItem('eternal_beam_background_image', url)
      localStorage.setItem('eternal_beam_background_theme_id', 'custom')
      localStorage.setItem('eternal_beam_background_theme_name', '내 사진')
    }
    reader.readAsDataURL(file)
    e.target.value = ''
  }

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mr = new MediaRecorder(stream)
      mediaRecorderRef.current = mr
      chunksRef.current = []

      mr.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunksRef.current.push(ev.data)
      }
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' })
        setVoiceRecording(blob)
        stream.getTracks().forEach((t) => t.stop())
      }

      mr.start()
      setIsRecording(true)
      setRecordingTime(0)
      timerRef.current = setInterval(() => {
        setRecordingTime((prev) => Math.min(prev + 1, 60))
      }, 1000)
    } catch {
      alert('마이크 권한이 필요합니다.')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }

  const handleVoiceComplete = async () => {
    if (!voiceRecording) return

    setShowVoiceUI(false)
    setIsMixing(true)

    const theme = selectedTheme
    const mixed = await mixAudioFiles(voiceRecording, theme?.bgmUrl)
    setMixedAudio(mixed)

    const reader = new FileReader()
    reader.onload = () => {
      const base64 = (reader.result as string).split(',')[1]
      if (base64) {
        localStorage.setItem('eternal_beam_mixed_audio', base64)
      }
    }
    reader.readAsDataURL(mixed)
    setIsMixing(false)
  }

  const handlePreviewAudio = () => {
    if (mixedAudio) {
      previewMixedAudio(mixedAudio)
    }
  }

  const voiceRecorded = !!mixedAudio
  const canProceed = !!(backgroundImage || selectedTheme)

  return (
    <div className="grid h-full w-full grid-cols-1 lg:grid-cols-[1fr_1fr] bg-transparent">
      {/* 왼쪽: 스크롤 영역 (헤더 + 업로드 + 테마 + 음성 + 버튼) - 좌우 50:50 균형 */}
      <div className="min-h-0 overflow-y-auto flex flex-col">
        {/* 1. 헤더 */}
        <div className="glass sticky top-0 z-10 px-6 py-6 border-0 rounded-b-2xl shrink-0">
          <div className="mb-2 flex items-center justify-between">
            <h1 className="font-headline text-3xl font-bold" style={{ color: '#2D2640' }}>
              배경 테마 선택
            </h1>
            <button
              onClick={() => {
                if (onBack) {
                  onBack()
                  return
                }
                if (editThemeOnly) {
                  localStorage.removeItem('eternal_beam_edit_theme_only')
                  onNavigate?.('preview')
                } else {
                  onNavigate?.('preview')
                }
              }}
              className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
              type="button"
            >
              <ArrowRight className="h-5 w-5 text-[#7C6B9B]" />
            </button>
          </div>
          <p className="text-sm" style={{ color: '#6B5B7A' }}>
            메인 사진과 함께 보여질 배경을 선택하세요
          </p>
        </div>

        {/* 모바일 전용: 상단 미리보기 (작은 화면) */}
        <div className="glass lg:hidden px-6 py-4 border-0 shrink-0">
          <p className="text-sm font-semibold text-gray-700 mb-2">📸 최종 미리보기</p>
          <div className="relative w-full max-w-[200px] mx-auto aspect-square bg-gray-100 rounded-2xl overflow-hidden">
            {isComposing && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
                <span className="text-white text-xs font-medium">합성 중...</span>
              </div>
            )}
            {(() => {
              const previewImage = composedPreview || cutoutImage
              if (previewImage) {
                if (isVideoContent && composedPreview && previewImage === composedPreview) {
                  return (
                    <video
                      src={previewImage}
                      className="w-full h-full object-cover"
                      muted
                      loop
                      playsInline
                    />
                  )
                }
                return <img src={previewImage} alt="미리보기" className="w-full h-full object-cover" />
              }
              return (
                <div className="w-full h-full flex items-center justify-center text-gray-400 text-xs text-center p-2">
                  배경 선택 전에는 누끼 결과(피사체), 배경 선택 후에는 최종 합성이 표시됩니다
                </div>
              )
            })()}
          </div>
        </div>

        {/* 2. 내 사진 업로드 */}
        <div className="px-6 mb-4">
        <label className="block">
          <div className="glass rounded-3xl p-4 cursor-pointer hover:shadow-lg transition-shadow flex items-center gap-3">
            <div className="w-12 h-12 rounded-full bg-purple-100 flex items-center justify-center shrink-0">
              <Upload className="w-6 h-6 text-purple-600" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="font-semibold text-gray-800">📁 내 사진 업로드</p>
              <p className="text-xs text-gray-500">직접 배경 이미지 선택</p>
            </div>
            <ChevronRight className="w-5 h-5 text-gray-400 shrink-0" />
          </div>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={handleBackgroundUpload}
          />
        </label>
        </div>

        {/* 4. 구분선: 또는 */}
        <div className="px-6 mb-4">
        <div className="flex items-center gap-3">
          <div className="flex-1 h-px bg-gray-300" />
          <span className="text-xs text-gray-500 font-semibold">또는</span>
          <div className="flex-1 h-px bg-gray-300" />
        </div>
        </div>

        {/* 5. ✨ 추천 테마 그리드 (6개) */}
        <div className="px-6 mb-6">
        <p className="text-sm font-semibold text-gray-700 mb-3">
          ✨ 추천 테마 선택
        </p>
        <div className="grid grid-cols-2 gap-4">
          {BGM_PRESETS.map((theme) => {
            const isPremium = theme.price > 0
            const owned = canUseTheme(theme.id)
            const locked = isPremium && !owned
            return (
            <button
              key={theme.id}
              onClick={() => handleThemeSelect(theme)}
              type="button"
              className={`rounded-3xl overflow-hidden transition-all text-left relative ${
                selectedTheme?.id === theme.id
                  ? 'ring-4 ring-purple-500 shadow-xl'
                  : 'shadow-md hover:shadow-lg'
              }`}
            >
              <div
                className="aspect-square relative"
                style={{ background: theme.gradient }}
              >
                {locked && (
                  <div className="absolute inset-0 flex items-center justify-center bg-black/50">
                    <Lock className="w-8 h-8 text-white" />
                  </div>
                )}
                {selectedTheme?.id === theme.id && !locked && (
                  <div className="absolute top-2 right-2 w-8 h-8 bg-white rounded-full flex items-center justify-center shadow">
                    <Check className="w-5 h-5 text-purple-600" />
                  </div>
                )}
              </div>
              <div className="glass p-3">
                <p className="font-semibold text-sm text-gray-800">{theme.name}</p>
                <p className="text-xs text-gray-500">
                  {theme.price === 0 ? '무료' : owned ? '보유' : `₩${theme.price.toLocaleString()}`}
                </p>
              </div>
            </button>
            )
          })}
        </div>
        </div>

        {/* 6. 음성 녹음 섹션 */}
        <div className="px-6 py-4">
        <div className="rounded-2xl p-6 bg-white shadow-md">
          <div className="mb-4 flex items-center gap-2">
            <Mic className="h-5 w-5 text-[#667eea]" />
            <h3 className="font-bold text-gray-800">음성 녹음</h3>
          </div>

          {!showVoiceUI && !voiceRecorded && (
            <button
              onClick={() => setShowVoiceUI(true)}
              type="button"
              className="flex w-full items-center justify-center gap-2 rounded-xl py-4 font-semibold bg-gradient-to-r from-purple-500/10 to-indigo-500/10 text-[#667eea]"
            >
              <Mic className="h-6 w-6" />
              마이크로 녹음하기
            </button>
          )}

          {showVoiceUI && !voiceRecorded && (
            <div className="space-y-4">
              <div className="text-center text-3xl font-bold text-[#667eea]">
                {Math.floor(recordingTime / 60)}:{(recordingTime % 60).toString().padStart(2, '0')}
              </div>
              <div className="flex justify-center gap-4">
                {!isRecording ? (
                  <button
                    onClick={startRecording}
                    type="button"
                    className="flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-r from-[#667eea] to-[#764ba2] text-white"
                  >
                    <Mic className="h-7 w-7" />
                  </button>
                ) : (
                  <button
                    onClick={stopRecording}
                    type="button"
                    className="flex h-14 w-14 items-center justify-center rounded-full bg-gradient-to-r from-[#FF6B6B] to-[#EE5A6F] text-white"
                  >
                    <Square className="h-7 w-7" fill="white" />
                  </button>
                )}
              </div>
              {voiceRecording && (
                <button
                  onClick={handleVoiceComplete}
                  type="button"
                  className="flex w-full items-center justify-center gap-2 rounded-xl py-3 font-semibold text-white bg-gradient-to-r from-[#8DD4C7] to-[#6BC4B5]"
                >
                  <Check className="h-5 w-5" />
                  녹음 완료 및 믹싱
                </button>
              )}
            </div>
          )}

          {isMixing && (
            <p className="py-4 text-center font-semibold text-[#667eea]">
              배경음악과 믹싱 중...
            </p>
          )}

          {voiceRecorded && (
            <div className="flex items-center gap-4">
              <Volume2 className="h-10 w-10 text-[#8DD4C7]" />
              <div className="flex-1 min-w-0">
                <p className="font-semibold text-gray-800">믹싱 완료</p>
                <p className="text-xs text-gray-500">목소리 + 배경음악</p>
              </div>
              <button
                onClick={handlePreviewAudio}
                type="button"
                className="flex items-center gap-2 rounded-xl bg-[#667eea] px-4 py-2 font-semibold text-white shrink-0"
              >
                <Play className="h-4 w-4" />
                미리듣기
              </button>
            </div>
          )}
        </div>
        </div>

        {/* 7. 하단 버튼: 다음: AI 변환 / 테마 변경 완료 */}
        <div className="sticky bottom-0 mt-auto px-6 py-4 bg-white/95 backdrop-blur-xl border-t border-black/5 shrink-0">
          <button
            onClick={() => {
              if (editThemeOnly) {
                localStorage.removeItem('eternal_beam_edit_theme_only')
                onNavigate?.('preview')
              } else {
                const themeId = selectedTheme?.id
                if (themeId && selectedTheme && selectedTheme.price > 0 && !canUseTheme(themeId)) {
                  localStorage.setItem('eternal_beam_pending_theme_id', themeId)
                  onNavigate?.('checkout')
                } else {
                  onNavigate?.('preview')
                }
              }
            }}
            disabled={!canProceed}
            type="button"
            className="w-full flex items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white bg-gradient-to-r from-[#667eea] to-[#764ba2] shadow-lg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {editThemeOnly ? '테마 변경 완료' : (selectedTheme && selectedTheme.price > 0 && !canUseTheme(selectedTheme.id) ? '결제하고 계속하기' : '다음: 미리보기')}
            <ChevronRight className="h-5 w-5" />
          </button>
        </div>
      </div>

      {/* 오른쪽: 프리뷰 패널 (lg 이상에서 좌우 50:50) */}
      <aside className="hidden lg:flex min-h-0 flex-col border-l border-gray-200 bg-white/80 backdrop-blur-sm p-6 overflow-y-auto">
        <p className="text-sm font-semibold text-gray-700 mb-3 shrink-0">
          📸 최종 미리보기
        </p>
        <div className="relative w-full max-w-[375px] aspect-square bg-gray-100 rounded-3xl overflow-hidden shadow-lg">
          {isComposing && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
              <span className="text-white text-sm font-medium">합성 중...</span>
            </div>
          )}
          {(() => {
            const previewImage = composedPreview || cutoutImage
            if (previewImage) {
              if (isVideoContent && composedPreview && previewImage === composedPreview) {
                return (
                  <video
                    src={previewImage}
                    className="w-full h-full object-cover"
                    muted
                    loop
                    playsInline
                  />
                )
              }
              return (
                <img
                  src={previewImage}
                  alt="합성 미리보기"
                  className="w-full h-full object-cover"
                />
              )
            }
            return (
              <div className="w-full h-full flex items-center justify-center text-gray-400 text-sm text-center px-4">
                배경 선택 전에는 누끼 결과(피사체), 배경 선택 후에는 최종 합성이 여기에 표시됩니다
              </div>
            )
          })()}
        </div>
      </aside>
    </div>
  )
}
