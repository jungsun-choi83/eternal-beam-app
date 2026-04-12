/**
 * 8단계 선형 플로우 라우팅
 * photoUpload → aiProcessing → themeSelection → preview → checkout(필요 시) → nfcPlayback
 * 뒤로 가기 시 이전 단계로 이동, SubjectSlotContext는 App 레벨에서 유지됨
 */

export const LINEAR_FLOW_ORDER: string[] = [
  'home',
  'photoUpload',
  'aiProcessing',
  'themeSelection',
  'preview',
  'checkout',
  'nfcPlayback',
]

export function getPreviousScreen(current: string): string | null {
  const idx = LINEAR_FLOW_ORDER.indexOf(current)
  if (idx <= 0) return 'home'
  return LINEAR_FLOW_ORDER[idx - 1]
}

export function getNextScreen(current: string): string | null {
  const idx = LINEAR_FLOW_ORDER.indexOf(current)
  if (idx < 0 || idx >= LINEAR_FLOW_ORDER.length - 1) return null
  return LINEAR_FLOW_ORDER[idx + 1]
}

export function isInLinearFlow(screen: string): boolean {
  return LINEAR_FLOW_ORDER.includes(screen)
}
