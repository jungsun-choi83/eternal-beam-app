/**
 * 미디어 타입 유틸리티 - 이미지/동영상 구분
 */

export type MediaType = 'image' | 'video'

const VIDEO_MIMES = ['video/mp4', 'video/webm', 'video/quicktime', 'video/x-msvideo']

export function getMediaType(file: File): MediaType {
  if (file.type.startsWith('video/') || VIDEO_MIMES.includes(file.type)) return 'video'
  return 'image'
}

export function getMediaTypeFromUrl(url: string): MediaType {
  if (!url) return 'image'
  if (url.startsWith('data:video/')) return 'video'
  if (url.match(/\.(mp4|webm|mov|avi)(\?|$)/i)) return 'video'
  return 'image'
}

export function isVideoUrl(url: string | null): boolean {
  if (!url) return false
  return getMediaTypeFromUrl(url) === 'video'
}
