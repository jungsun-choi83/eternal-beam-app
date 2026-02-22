/**
 * 영상 합성 API 클라이언트
 */

const API_BASE = '' // Vite proxy → localhost:3001

export interface MusicLibraryItem {
  id: string
  name: string
  url: string
}

export async function fetchMusicList(): Promise<MusicLibraryItem[]> {
  const res = await fetch(`${API_BASE}/api/music/list`)
  if (!res.ok) throw new Error('음악 목록을 불러올 수 없습니다.')
  return res.json()
}

export interface SynthesizeParams {
  voiceBlob: Blob
  musicBlob?: Blob | null
  musicLibraryId?: string
  imageBlob?: Blob | null
  voiceVolume: number
  musicVolume: number
  musicTrimStart: number
  musicTrimEnd: number
  fadeOutSeconds: number
  userId?: string
  planType?: 'basic' | 'premium' | 'lifetime'
  contentId?: string
  onProgress?: (percent: number) => void
}

export async function synthesize(params: SynthesizeParams): Promise<{ outputUrl: string }> {
  const form = new FormData()
  form.append('voice', params.voiceBlob, 'voice.webm')
  form.append('voiceVolume', String(params.voiceVolume))
  form.append('musicVolume', String(params.musicVolume))
  form.append('musicTrimStart', String(params.musicTrimStart))
  form.append('musicTrimEnd', String(params.musicTrimEnd))
  form.append('fadeOutSeconds', String(params.fadeOutSeconds))

  if (params.musicBlob) {
    form.append('music', params.musicBlob, 'music.mp3')
  } else if (params.musicLibraryId) {
    form.append('musicLibraryId', params.musicLibraryId)
  }

  if (params.imageBlob) {
    form.append('image', params.imageBlob, 'image.jpg')
  }

  if (params.userId) form.append('userId', params.userId)
  if (params.planType) form.append('planType', params.planType)
  if (params.contentId) form.append('contentId', params.contentId)

  const xhr = new XMLHttpRequest()

  return new Promise((resolve, reject) => {
    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const uploadPercent = Math.round((e.loaded / e.total) * 50)
        params.onProgress?.(uploadPercent)
      }
    })

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        params.onProgress?.(100)
        const data = JSON.parse(xhr.responseText)
        resolve({ outputUrl: data.outputUrl || data.outputPath || '' })
      } else {
        let errMsg = '합성 중 오류가 발생했습니다.'
        try {
          const err = JSON.parse(xhr.responseText)
          errMsg = err.error || errMsg
        } catch {}
        reject(new Error(errMsg))
      }
    })

    xhr.addEventListener('error', () => reject(new Error('네트워크 오류')))
    xhr.addEventListener('abort', () => reject(new Error('요청이 취소되었습니다.')))

    xhr.open('POST', `${API_BASE}/api/synthesize`)
    xhr.send(form)
  })
}

/** Pi 기기에 합성된 MP4 업로드 */
export async function uploadToDevice(
  deviceId: string,
  outputPath: string
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/device/${deviceId}/upload`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ outputPath }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.error || '기기 전송 실패')
  }
}

/** Pi 기기 재생 트리거 */
export async function triggerDevicePlay(deviceId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/device/${deviceId}/play`, {
    method: 'POST',
  })
  if (!res.ok) throw new Error('재생 명령 전송 실패')
}
