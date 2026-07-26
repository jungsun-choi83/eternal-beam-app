/**
 * PayPal 테마 결제 API 클라이언트 — 서버 /api/paypal/* 래퍼.
 * 실제 결제 확정(capture) 검증은 항상 서버에서 이뤄지므로, 여기서 받은 success는
 * 서버가 PayPal에 직접 확인한 결과다.
 */

import { getVideoApiBaseUrl } from '@/app/services/videoProcessingApi'
import { getEternalBeamUserId } from '@/lib/eternal-beam-user'

export interface CreatePaypalOrderResult {
  order_id: string
  amount_usd: string
  currency: string
  theme_key: string
  status: string
}

export interface CapturePaypalOrderResult {
  success: boolean
  status: string
  theme_key: string
  order_id: string
  payment_id?: string | null
  message?: string
}

async function parseJsonOrThrow<T>(res: Response, fallbackMessage: string): Promise<T> {
  let body: Record<string, unknown> = {}
  try {
    body = (await res.json()) as Record<string, unknown>
  } catch {
    /* ignore */
  }
  if (!res.ok) {
    const detail = typeof body.detail === 'string' ? body.detail : fallbackMessage
    throw new Error(detail)
  }
  return body as T
}

export async function createPaypalOrder(themeKey: string): Promise<CreatePaypalOrderResult> {
  const base = getVideoApiBaseUrl()
  const res = await fetch(`${base}/api/paypal/create-order`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: getEternalBeamUserId(), theme_key: themeKey }),
  })
  return parseJsonOrThrow<CreatePaypalOrderResult>(res, 'PayPal 주문 생성에 실패했습니다.')
}

export async function capturePaypalOrder(
  themeKey: string,
  orderId: string
): Promise<CapturePaypalOrderResult> {
  const base = getVideoApiBaseUrl()
  const res = await fetch(`${base}/api/paypal/capture-order`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_id: getEternalBeamUserId(),
      theme_key: themeKey,
      order_id: orderId,
    }),
  })
  return parseJsonOrThrow<CapturePaypalOrderResult>(res, 'PayPal 결제 확인에 실패했습니다.')
}
