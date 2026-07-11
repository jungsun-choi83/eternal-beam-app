/** 킥스타터/기기 데모 — 메인 홈 UI + 포레스트 + NFC */

export const DEVICE_DEMO_GOYA_CUTOUT = '/demo/goya-cutout.png'

export function isDeviceKickstarterDemo(): boolean {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  const demo = params.get('demo')?.trim().toLowerCase()
  return demo === 'device' || demo === 'kickstarter'
}
