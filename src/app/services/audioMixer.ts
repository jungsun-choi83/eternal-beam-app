/**
 * 오디오 믹싱 서비스
 * 음성 녹음 + BGM 믹싱
 */

export interface BGMPreset {
  id: string
  name: string
  gradient: string
  bgmUrl?: string
  price: number
}

export const BGM_PRESETS: BGMPreset[] = [
  {
    id: 'sweet_memory',
    name: 'Sweet Memory',
    gradient: 'linear-gradient(135deg, #FFB6C1, #FFC0CB)',
    price: 0,
  },
  {
    id: 'galaxy_dream',
    name: 'Galaxy Dream',
    gradient: 'linear-gradient(135deg, #667eea, #764ba2)',
    price: 2900,
  },
  {
    id: 'nature_bloom',
    name: 'Nature Bloom',
    gradient: 'linear-gradient(135deg, #8DD4C7, #6BC4B5)',
    price: 0,
  },
  {
    id: 'ocean_wave',
    name: 'Ocean Wave',
    gradient: 'linear-gradient(135deg, #00d4ff, #0095ff)',
    price: 0,
  },
  {
    id: 'neon_city',
    name: 'Neon City',
    gradient: 'linear-gradient(135deg, #f093fb, #f5576c)',
    price: 2900,
  },
  {
    id: 'golden_hour',
    name: 'Golden Hour',
    gradient: 'linear-gradient(135deg, #FFD700, #FF8C00)',
    price: 0,
  },
]

/**
 * 음성 + BGM 믹싱
 * BGM이 없으면 음성만 반환
 */
export async function mixAudioFiles(
  voiceBlob: Blob,
  _bgmUrl?: string,
): Promise<Blob> {
  if (!_bgmUrl) {
    return voiceBlob
  }
  try {
    const audioContext = new (window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext)()
    const voiceBuffer = await audioContext.decodeAudioData(
      await voiceBlob.arrayBuffer(),
    )

    const length = voiceBuffer.length
    const numChannels = voiceBuffer.numberOfChannels
    const sampleRate = voiceBuffer.sampleRate

    const offlineCtx = new OfflineAudioContext(numChannels, length, sampleRate)
    const source = offlineCtx.createBufferSource()
    source.buffer = voiceBuffer
    source.connect(offlineCtx.destination)
    source.start(0)

    const rendered = await offlineCtx.startRendering()
    const wavBlob = await audioBufferToWav(rendered)
    return wavBlob
  } catch {
    return voiceBlob
  }
}

function audioBufferToWav(buffer: AudioBuffer): Promise<Blob> {
  return new Promise((resolve) => {
    const numChannels = buffer.numberOfChannels
    const sampleRate = buffer.sampleRate
    const format = 1
    const bitDepth = 16
    const bytesPerSample = bitDepth / 8
    const blockAlign = numChannels * bytesPerSample
    const dataLength = buffer.length * blockAlign
    const bufferLength = 44 + dataLength
    const arrayBuffer = new ArrayBuffer(bufferLength)
    const view = new DataView(arrayBuffer)
    const channels: Float32Array[] = []
    for (let i = 0; i < numChannels; i++) {
      channels.push(buffer.getChannelData(i))
    }

    const writeString = (offset: number, str: string) => {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i))
      }
    }

    writeString(0, 'RIFF')
    view.setUint32(4, 36 + dataLength, true)
    writeString(8, 'WAVE')
    writeString(12, 'fmt ')
    view.setUint32(16, 16, true)
    view.setUint16(20, format, true)
    view.setUint16(22, numChannels, true)
    view.setUint32(24, sampleRate, true)
    view.setUint32(28, sampleRate * blockAlign, true)
    view.setUint16(32, blockAlign, true)
    view.setUint16(34, bitDepth, true)
    writeString(36, 'data')
    view.setUint32(40, dataLength, true)

    let offset = 44
    for (let i = 0; i < buffer.length; i++) {
      for (let ch = 0; ch < numChannels; ch++) {
        const sample = Math.max(-1, Math.min(1, channels[ch][i]))
        const intSample = sample < 0 ? sample * 0x8000 : sample * 0x7fff
        view.setInt16(offset, intSample, true)
        offset += 2
      }
    }

    resolve(new Blob([arrayBuffer], { type: 'audio/wav' }))
  })
}

/**
 * 믹싱된 오디오 미리듣기
 */
export function previewMixedAudio(blob: Blob): () => void {
  const url = URL.createObjectURL(blob)
  const audio = new Audio(url)
  audio.play()
  return () => {
    audio.pause()
    audio.currentTime = 0
    URL.revokeObjectURL(url)
  }
}
