/**
 * PayPal JS SDK 동적 로더.
 * Client ID는 공개 값(비밀 아님)이라 <script> 태그에 그대로 넣어도 안전합니다.
 * 실제 결제 검증(주문 생성/캡처)은 항상 서버(paypal-api.ts → /api/paypal/*)에서 합니다.
 */

declare global {
  interface Window {
    paypal?: PaypalNamespace
  }
}

export interface PaypalButtonsActions {
  order: {
    create: (options: Record<string, unknown>) => Promise<string>
    capture: () => Promise<unknown>
  }
}

export interface PaypalButtonsOptions {
  style?: Record<string, unknown>
  createOrder: () => Promise<string>
  onApprove: (data: { orderID: string }) => Promise<void> | void
  onError?: (err: unknown) => void
  onCancel?: () => void
}

export interface PaypalButtonsInstance {
  render: (containerSelectorOrElement: string | HTMLElement) => Promise<void>
  close?: () => void
}

export interface PaypalNamespace {
  Buttons: (options: PaypalButtonsOptions) => PaypalButtonsInstance
}

let loadPromise: Promise<PaypalNamespace> | null = null

export function getPaypalClientId(): string {
  // .trim() — Vercel 등 대시보드에서 값에 공백/개행이 섞여 들어가도 안전하게 사용
  return String(import.meta.env.VITE_PAYPAL_CLIENT_ID ?? '').trim()
}

export function isPaypalConfigured(): boolean {
  return getPaypalClientId().length > 0
}

/** PayPal SDK <script>를 한 번만 삽입하고 window.paypal이 준비되면 resolve. */
export function loadPaypalSdk(currency = 'USD'): Promise<PaypalNamespace> {
  if (typeof window === 'undefined') {
    return Promise.reject(new Error('PayPal SDK는 브라우저에서만 로드할 수 있습니다.'))
  }
  if (window.paypal) return Promise.resolve(window.paypal)
  if (loadPromise) return loadPromise

  const clientId = getPaypalClientId()
  if (!clientId) {
    return Promise.reject(
      new Error('VITE_PAYPAL_CLIENT_ID가 설정되지 않았습니다. Vercel 환경변수를 확인하세요.')
    )
  }

  loadPromise = new Promise<PaypalNamespace>((resolve, reject) => {
    const existing = document.getElementById('paypal-sdk-script') as HTMLScriptElement | null
    if (existing && window.paypal) {
      resolve(window.paypal)
      return
    }

    const script = existing ?? document.createElement('script')
    script.id = 'paypal-sdk-script'
    script.src = `https://www.paypal.com/sdk/js?client-id=${encodeURIComponent(
      clientId
    )}&currency=${encodeURIComponent(currency)}&intent=capture`
    script.async = true
    script.onload = () => {
      if (window.paypal) resolve(window.paypal)
      else reject(new Error('PayPal SDK 로드에 실패했습니다.'))
    }
    script.onerror = () => reject(new Error('PayPal SDK 스크립트를 불러올 수 없습니다.'))
    if (!existing) document.head.appendChild(script)
  }).catch((err) => {
    loadPromise = null
    throw err
  })

  return loadPromise
}
