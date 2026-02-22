import {
  ArrowLeft,
  Mic,
  Music2,
  Upload,
  Library,
  Check,
  Loader2,
} from 'lucide-react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'
import { VoiceRecorder } from './VoiceRecorder'
import {
  fetchMusicList,
  synthesize,
  type MusicLibraryItem,
} from '../../services/synthesisService'
import { getUserQuota, ensureUserQuota } from '../../services/quotaService'
import { UpgradePopup } from './UpgradePopup'
import { auth } from '../../config/firebase'
import { useState, useEffect, useRef } from 'react'

type MusicSource = 'none' | 'file' | 'library'

interface RecordScreenProps {
  onNavigate?: (screen: string) => void
}

export function RecordScreen({ onNavigate }: RecordScreenProps) {
  const { t } = useLanguage()
  const { setAudioBlob } = useImage()
  const [voiceBlob, setVoiceBlob] = useState<Blob | null>(null)
  const [musicSource, setMusicSource] = useState<MusicSource>('none')
  const [musicFile, setMusicFile] = useState<File | null>(null)
  const [musicLibraryId, setMusicLibraryId] = useState<string>('')
  const [voiceVolume, setVoiceVolume] = useState(80)
  const [musicVolume, setMusicVolume] = useState(20)
  const [musicTrimStart, setMusicTrimStart] = useState(0)
  const [musicTrimEnd, setMusicTrimEnd] = useState(0)
  const [musicList, setMusicList] = useState<MusicLibraryItem[]>([])
  const [isSynthesizing, setIsSynthesizing] = useState(false)
  const [synthesisProgress, setSynthesisProgress] = useState(0)
  const [synthesisError, setSynthesisError] = useState<string | null>(null)
  const [showUpgradePopup, setShowUpgradePopup] = useState(false)
  const [quota, setQuota] = useState<Awaited<ReturnType<typeof getUserQuota>>>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const userId = auth.currentUser?.uid ?? 'anonymous'

  useEffect(() => {
    ensureUserQuota(userId)
      .then(() => getUserQuota(userId))
      .then(setQuota)
      .catch(() => getUserQuota(userId).then(setQuota))
  }, [userId])

  useEffect(() => {
    fetchMusicList().then(setMusicList).catch(() => setMusicList([]))
  }, [])

  const handleVoiceComplete = (blob: Blob | null) => {
    setVoiceBlob(blob)
    setAudioBlob(blob ?? null)
  }

  const handleMusicFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f && /\.(mp3|m4a|aac|wav)$/i.test(f.name)) {
      setMusicFile(f)
      setMusicSource('file')
      setMusicLibraryId('')
    }
    e.target.value = ''
  }

  const handleLibrarySelect = (id: string) => {
    setMusicLibraryId(id)
    setMusicSource('library')
    setMusicFile(null)
  }

  const clearMusic = () => {
    setMusicSource('none')
    setMusicFile(null)
    setMusicLibraryId('')
  }

  const handleSynthesizeAndNext = async () => {
    if (!voiceBlob) return
    if (quota?.is_over_generation) {
      setShowUpgradePopup(true)
      return
    }
    if (quota?.is_over_storage) {
      setShowUpgradePopup(true)
      return
    }
    setIsSynthesizing(true)
    setSynthesisError(null)
    setSynthesisProgress(0)

    try {
      const mainPhoto = localStorage.getItem('eternal_beam_main_photo')
      let imageBlob: Blob | null = null
      if (mainPhoto && mainPhoto.startsWith('data:')) {
        const res = await fetch(mainPhoto)
        imageBlob = await res.blob()
      }

      const musicBlob = musicSource === 'file' && musicFile ? musicFile : undefined

      const { outputUrl } = await synthesize({
        voiceBlob,
        musicBlob,
        musicLibraryId: musicSource === 'library' ? musicLibraryId : undefined,
        imageBlob,
        voiceVolume,
        musicVolume,
        musicTrimStart,
        musicTrimEnd,
        fadeOutSeconds: 2,
        userId,
        planType: (quota?.plan_type ?? 'basic') as 'basic' | 'premium' | 'lifetime',
        onProgress: setSynthesisProgress,
      })

      localStorage.setItem('eternal_beam_synthesized_mp4_url', outputUrl)
      onNavigate?.('storageChoice')
    } catch (err) {
      setSynthesisError(err instanceof Error ? err.message : '합성 실패')
    } finally {
      setIsSynthesizing(false)
      setSynthesisProgress(0)
    }
  }

  const canProceed = !!voiceBlob
  const hasMusic = musicSource !== 'none'

  return (
    <div className="relative h-full w-full overflow-y-auto bg-transparent">
      <div className="glass sticky top-0 z-10 px-6 py-4 border-0 rounded-b-2xl">
        <div className="mb-2 flex items-center justify-between">
          <h1 className="font-headline text-2xl font-bold" style={{ color: '#2D2640' }}>
            {t('record.title')}
          </h1>
          <button
            onClick={() => onNavigate?.('storageChoice')}
            type="button"
            className="glass-strong flex h-10 w-10 items-center justify-center rounded-full shrink-0"
          >
            <ArrowLeft className="h-5 w-5 text-[#7C6B9B]" />
          </button>
        </div>
        <p className="text-sm" style={{ color: '#6B5B7A' }}>
          {t('record.desc')}
        </p>
      </div>

      <div className="px-6 py-4 space-y-6">
        {/* 음성 녹음 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-700 mb-2 flex items-center gap-2">
            <Mic className="h-4 w-4" />
            음성 녹음
          </h3>
          <VoiceRecorder onComplete={handleVoiceComplete} />
        </section>

        {/* 음악 소스 선택 */}
        <section>
          <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2">
            <Music2 className="h-4 w-4" />
            배경 음악 (선택)
          </h3>
          <div className="flex gap-2 flex-wrap">
            <button
              type="button"
              onClick={() => { setMusicSource('none'); clearMusic() }}
              className={`rounded-xl px-4 py-2 text-sm font-medium ${
                musicSource === 'none'
                  ? 'bg-[#667eea] text-white'
                  : 'glass text-gray-600'
              }`}
            >
              녹음만
            </button>
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className={`rounded-xl px-4 py-2 text-sm font-medium flex items-center gap-2 ${
                musicSource === 'file'
                  ? 'bg-[#667eea] text-white'
                  : 'glass text-gray-600'
              }`}
            >
              <Upload className="h-4 w-4" />
              내 폰에서 가져오기
            </button>
            <button
              type="button"
              onClick={() => { setMusicSource('library'); setMusicFile(null) }}
              className={`rounded-xl px-4 py-2 text-sm font-medium flex items-center gap-2 ${
                musicSource === 'library'
                  ? 'bg-[#667eea] text-white'
                  : 'glass text-gray-600'
              }`}
            >
              <Library className="h-4 w-4" />
              기본 라이브러리
            </button>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*"
            className="hidden"
            onChange={handleMusicFileSelect}
          />

          {musicSource === 'file' && musicFile && (
            <p className="mt-2 text-sm text-gray-600 truncate">
              {musicFile.name}
            </p>
          )}

          {musicSource === 'library' && musicList.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              {musicList.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => handleLibrarySelect(m.id)}
                  className={`rounded-xl p-3 text-left text-sm font-medium ${
                    musicLibraryId === m.id
                      ? 'ring-2 ring-[#667eea] bg-purple-50'
                      : 'glass'
                  }`}
                >
                  {m.name}
                </button>
              ))}
            </div>
          )}
        </section>

        {/* 볼륨 밸런스 (듀얼 슬라이더) */}
        <section>
          <h3 className="text-sm font-semibold text-gray-700 mb-3">볼륨 밸런스</h3>
          <div className="space-y-3">
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span>목소리</span>
                <span className="font-bold text-[#667eea]">{voiceVolume}%</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={voiceVolume}
                onChange={(e) => setVoiceVolume(Number(e.target.value))}
                className="w-full h-2 rounded-full accent-[#667eea]"
              />
            </div>
            <div>
              <div className="flex justify-between text-xs mb-1">
                <span>배경 음악</span>
                <span className="font-bold text-[#667eea]">{musicVolume}%</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={musicVolume}
                onChange={(e) => setMusicVolume(Number(e.target.value))}
                className="w-full h-2 rounded-full accent-[#667eea]"
              />
            </div>
          </div>
        </section>

        {/* 트리밍 (음악 선택 시) */}
        {hasMusic && (
          <section>
            <h3 className="text-sm font-semibold text-gray-700 mb-2">음악 구간 선택</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">시작 (초)</label>
                <input
                  type="number"
                  min="0"
                  value={musicTrimStart}
                  onChange={(e) => setMusicTrimStart(Math.max(0, Number(e.target.value)))}
                  className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">끝 (초, 0=전체)</label>
                <input
                  type="number"
                  min="0"
                  value={musicTrimEnd || ''}
                  onChange={(e) => setMusicTrimEnd(Number(e.target.value) || 0)}
                  placeholder="전체"
                  className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm"
                />
              </div>
            </div>
          </section>
        )}

        {/* 합성 버튼 */}
        <div className="sticky bottom-0 pt-4 pb-8">
          {synthesisError && (
            <p className="mb-3 text-sm text-red-600">{synthesisError}</p>
          )}
          {isSynthesizing && (
            <div className="mb-3">
              <div className="flex items-center gap-2 text-sm text-[#667eea]">
                <Loader2 className="h-4 w-4 animate-spin" />
                합성 중... {synthesisProgress}%
              </div>
              <div className="h-2 rounded-full bg-gray-200 overflow-hidden mt-1">
                <div
                  className="h-full bg-[#667eea] transition-all"
                  style={{ width: `${synthesisProgress}%` }}
                />
              </div>
            </div>
          )}
          <button
            onClick={handleSynthesizeAndNext}
            disabled={!canProceed || isSynthesizing}
            type="button"
            className="w-full flex items-center justify-center gap-2 rounded-2xl py-4 font-bold text-white disabled:opacity-50 disabled:cursor-not-allowed"
            style={{
              background: 'linear-gradient(135deg, #B8A4D4 0%, #D4A89C 50%, #C9A8D4 100%)',
              boxShadow: '0 8px 24px rgba(102, 126, 234, 0.3)',
            }}
          >
            {isSynthesizing ? (
              <Loader2 className="h-5 w-5 animate-spin" />
            ) : (
              <>
                <Check className="h-5 w-5" />
                합성 후 다음
              </>
            )}
          </button>
        </div>
      </div>

      {showUpgradePopup && (
        <UpgradePopup
          reason={quota?.is_over_generation ? 'generation' : 'storage'}
          onClose={() => setShowUpgradePopup(false)}
          onSelectPlan={() => {
            onNavigate?.('checkout')
            setShowUpgradePopup(false)
          }}
        />
      )}
    </div>
  )
}
