// src/app/services/zoeHologramService.ts

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) || ''

/**
 * ZoeDepth 서버(API /api/zoe-hologram)에 이미지를 보내서
 * 깊이맵 PNG URL을 받아오는 함수
 */
export async function createZoeDepthPreview(
  imageBlob: Blob,
): Promise<string> {
  const form = new FormData()
  form.append('image', imageBlob, 'photo.jpg')

  const res = await fetch(`${API_BASE}/api/zoe-hologram`, {
    method: 'POST',
    body: form,
  })

  if (!res.ok) {
    let msg = 'ZoeDepth 변환 중 오류가 발생했습니다.'
    try {
      const err = await res.json()
      if (err.error) msg = err.error
    } catch {
      // ignore
    }
    throw new Error(msg)
  }

  const data = (await res.json()) as {
    success: boolean
    depthUrl: string
  }

  if (!data.success || !data.depthUrl) {
    throw new Error('ZoeDepth 응답이 올바르지 않습니다.')
  }

  // 프리뷰에서 바로 쓸 수 있는 URL (/output/...)
  return data.depthUrl
}