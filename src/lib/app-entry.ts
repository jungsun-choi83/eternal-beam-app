/** URL/경로로 공개 포레스트 체험 진입 감지 */

export function isPublicForestEntry(): boolean {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  if (params.get('experience') === 'forest') return true
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  return path === '/forest'
}

export const PUBLIC_FOREST_URL = 'https://device.eternalbeam.com/forest'
