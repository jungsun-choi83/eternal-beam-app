/** 웹에서 NFC 태그 시뮬레이션 (Pi NFC와 촬영 타이밍 맞출 때 Enter 또는 API) */

export const NFC_ACTIVATE_EVENT = 'eternalbeam:nfc'

export function triggerNfcActivation(): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent(NFC_ACTIVATE_EVENT))
}

export function subscribeNfcActivation(onActivate: () => void): () => void {
  if (typeof window === 'undefined') return () => {}

  const win = window as Window & { __eternalBeamNfcTag?: () => void }
  win.__eternalBeamNfcTag = onActivate

  const onEvent = () => onActivate()
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.repeat) onActivate()
  }

  window.addEventListener(NFC_ACTIVATE_EVENT, onEvent)
  window.addEventListener('keydown', onKey)

  return () => {
    delete win.__eternalBeamNfcTag
    window.removeEventListener(NFC_ACTIVATE_EVENT, onEvent)
    window.removeEventListener('keydown', onKey)
  }
}
