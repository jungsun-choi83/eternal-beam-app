import { Mic, Square, Play, Pause, Trash2, Check } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import { useLanguage } from '../../contexts/LanguageContext'
import { useImage } from '../../contexts/ImageContext'

interface VoiceRecorderProps {
  onComplete?: (audioBlob: Blob | null) => void
}

export function VoiceRecorder({ onComplete }: VoiceRecorderProps) {
  const { t } = useLanguage()
  const { setAudioBlob } = useImage()
  const [isRecording, setIsRecording] = useState(false)
  const [isPaused, setIsPaused] = useState(false)
  const [audioURL, setAudioURL] = useState<string | null>(null)
  const [recordingTime, setRecordingTime] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)

  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const lastBlobRef = useRef<Blob | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      if (audioURL) URL.revokeObjectURL(audioURL)
    }
  }, [audioURL])

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream)
      mediaRecorderRef.current = mediaRecorder
      audioChunksRef.current = []
      lastBlobRef.current = null

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data)
        }
      }

      mediaRecorder.onstop = () => {
        const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        lastBlobRef.current = blob
        const url = URL.createObjectURL(blob)
        setAudioURL(url)
        setAudioBlob(blob)
        onComplete?.(blob)
        stream.getTracks().forEach((track) => track.stop())
      }

      mediaRecorder.start()
      setIsRecording(true)
      setRecordingTime(0)

      timerRef.current = setInterval(() => {
        setRecordingTime((prev) => Math.min(prev + 1, 60))
      }, 1000)
    } catch (error) {
      console.error('마이크 접근 실패:', error)
      alert('마이크 접근 권한이 필요합니다.')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
      setIsPaused(false)
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }

  const pauseRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      if (isPaused) {
        mediaRecorderRef.current.resume()
        timerRef.current = setInterval(() => {
          setRecordingTime((prev) => Math.min(prev + 1, 60))
        }, 1000)
      } else {
        mediaRecorderRef.current.pause()
        if (timerRef.current) clearInterval(timerRef.current)
      }
      setIsPaused(!isPaused)
    }
  }

  const deleteRecording = () => {
    if (audioURL) URL.revokeObjectURL(audioURL)
    setAudioURL(null)
    setRecordingTime(0)
    audioChunksRef.current = []
    lastBlobRef.current = null
    setAudioBlob(null)
    onComplete?.(null)
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current = null
    }
    setIsPlaying(false)
  }

  const playRecording = () => {
    if (audioURL) {
      if (!audioRef.current) {
        audioRef.current = new Audio(audioURL)
        audioRef.current.onended = () => setIsPlaying(false)
      }

      if (isPlaying) {
        audioRef.current.pause()
        setIsPlaying(false)
      } else {
        audioRef.current.play()
        setIsPlaying(true)
      }
    }
  }

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const handleComplete = () => {
    const blob = lastBlobRef.current
    if (blob) {
      onComplete?.(blob)
    } else if (audioURL && audioChunksRef.current.length > 0) {
      const blob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
      onComplete?.(blob)
    }
  }

  return (
    <div className="w-full max-w-sm">
      {/* Waveform Visualization */}
      <div
        className="mb-6 flex h-32 w-full items-center justify-center gap-1 rounded-2xl px-4"
        style={{
          background:
            'linear-gradient(135deg, rgba(102, 126, 234, 0.1) 0%, rgba(118, 75, 162, 0.1) 100%)',
          border: '2px solid rgba(102, 126, 234, 0.2)',
        }}
      >
        {isRecording ? (
          Array.from({ length: 24 }).map((_, i) => (
            <div
              key={i}
              className="w-1 rounded-full transition-all"
              style={{
                height: `${20 + Math.random() * 80}%`,
                background: 'linear-gradient(180deg, #667eea 0%, #764ba2 100%)',
                animation: 'wave 0.5s ease-in-out infinite',
                animationDelay: `${i * 0.05}s`,
              }}
            />
          ))
        ) : audioURL ? (
          <div className="text-center">
            <Check
              className="mx-auto mb-2 h-12 w-12"
              style={{ color: '#8DD4C7' }}
            />
            <p
              className="text-sm font-semibold"
              style={{ color: '#667eea' }}
            >
              {t('voice.recorded') || '녹음 완료'}
            </p>
          </div>
        ) : (
          <Mic className="h-12 w-12" style={{ color: '#cbd5e0' }} />
        )}
      </div>

      {/* Timer */}
      <div className="mb-6 text-center">
        <div
          className="text-4xl font-bold"
          style={{
            fontFamily: 'monospace',
            color: isRecording ? '#667eea' : '#a0aec0',
          }}
        >
          {formatTime(recordingTime)}
        </div>
      </div>

      {/* Controls */}
      <div className="mb-6 flex items-center justify-center gap-4">
        {!isRecording && !audioURL && (
          <button
            onClick={startRecording}
            type="button"
            className="flex h-16 w-16 items-center justify-center rounded-full"
            style={{
              background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
              boxShadow: '0 8px 24px rgba(102, 126, 234, 0.4)',
            }}
          >
            <Mic className="h-8 w-8" style={{ color: 'white' }} />
          </button>
        )}

        {isRecording && (
          <>
            <button
              onClick={pauseRecording}
              type="button"
              className="flex h-14 w-14 items-center justify-center rounded-full"
              style={{
                background: 'white',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.1)',
              }}
            >
              {isPaused ? (
                <Play className="h-6 w-6" style={{ color: '#667eea' }} />
              ) : (
                <Pause className="h-6 w-6" style={{ color: '#667eea' }} />
              )}
            </button>

            <button
              onClick={stopRecording}
              type="button"
              className="flex h-16 w-16 items-center justify-center rounded-full"
              style={{
                background: 'linear-gradient(135deg, #FF6B6B 0%, #EE5A6F 100%)',
                boxShadow: '0 8px 24px rgba(255, 107, 107, 0.4)',
              }}
            >
              <Square className="h-8 w-8" style={{ color: 'white' }} />
            </button>
          </>
        )}

        {audioURL && !isRecording && (
          <>
            <button
              onClick={playRecording}
              type="button"
              className="flex h-14 w-14 items-center justify-center rounded-full"
              style={{
                background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
                boxShadow: '0 4px 16px rgba(102, 126, 234, 0.3)',
              }}
            >
              {isPlaying ? (
                <Pause className="h-6 w-6" style={{ color: 'white' }} />
              ) : (
                <Play className="h-6 w-6" style={{ color: 'white' }} />
              )}
            </button>

            <button
              onClick={deleteRecording}
              type="button"
              className="flex h-14 w-14 items-center justify-center rounded-full"
              style={{
                background: 'white',
                boxShadow: '0 4px 16px rgba(0, 0, 0, 0.1)',
              }}
            >
              <Trash2 className="h-6 w-6" style={{ color: '#FF6B6B' }} />
            </button>

            <button
              onClick={handleComplete}
              type="button"
              className="flex h-14 w-14 items-center justify-center rounded-full"
              style={{
                background: 'linear-gradient(135deg, #8DD4C7 0%, #6BC4B5 100%)',
                boxShadow: '0 4px 16px rgba(141, 212, 199, 0.3)',
              }}
            >
              <Check className="h-6 w-6" style={{ color: 'white' }} />
            </button>
          </>
        )}
      </div>

      <style>{`
        @keyframes wave {
          0%, 100% { transform: scaleY(1); }
          50% { transform: scaleY(1.5); }
        }
      `}</style>
    </div>
  )
}
