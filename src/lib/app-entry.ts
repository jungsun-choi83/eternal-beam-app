/** URL/경로로 공개 포레스트 체험 진입 감지 */

export function isPublicForestEntry(): boolean {
  if (typeof window === 'undefined') return false
  const params = new URLSearchParams(window.location.search)
  if (params.get('experience') === 'forest') return true
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  return path === '/forest'
}

export const PUBLIC_FOREST_URL = 'https://device.eternalbeam.com/forest'

/**
 * Toss 결제 복귀 경로 감지.
 *
 * Toss 는 결제창을 마친 뒤 우리가 넘긴 successUrl/failUrl 로 **페이지를 이동**시킨다
 * (팝업이 아니라 리다이렉트다). 그래서 앱은 그 경로로 부팅될 수 있어야 하고,
 * 그때 화면이 "QR 연결"로 떨어지면 결제가 허공으로 사라진 것처럼 보인다.
 *
 * vercel.json 의 SPA 리라이트가 이미 /billing/* 를 index.html 로 보내므로
 * 인프라 변경은 필요 없다 — 여기서 경로만 알아보면 된다.
 */
export type BillingReturn = 'success' | 'fail' | null

export function billingReturnEntry(): BillingReturn {
  if (typeof window === 'undefined') return null
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (path === '/billing/success') return 'success'
  if (path === '/billing/fail') return 'fail'
  return null
}

/**
 * Toss 가 성공 URL 에 실어 주는 값들.
 *
 * 여기 있는 이유: 이 파일은 **의존성이 없다**. toss-billing.ts 는 `@/` 별칭을
 * import 하는데 node --test 는 그 별칭을 풀지 못한다(Vite 만 푼다). 순수 함수를
 * 여기 두면 앱과 테스트가 같은 코드를 본다.
 */
export function readBillingRedirectParams(search: string): {
  authKey: string | null
  customerKey: string | null
  orderId: string | null
  planId: string | null
} {
  const p = new URLSearchParams(search)
  return {
    authKey: p.get('authKey'),
    customerKey: p.get('customerKey'),
    orderId: p.get('orderId'),
    planId: p.get('planId'),
  }
}

/**
 * 테마 일회성 결제 복귀 경로 감지.
 *
 * Toss 는 결제창을 마친 뒤 successUrl/failUrl 로 **페이지를 이동**시킨다.
 * 구독 복귀(/billing/*)와 **경로를 나눈** 이유: 두 흐름은 확인 엔드포인트도
 * 결과 화면도 다르다. 같은 경로를 공유하면 테마 결제가 구독 confirm 을 타게 되고,
 * 그건 "테마 구매가 구독을 건드리지 않는다"는 계약을 정면으로 깬다.
 *
 * vercel.json 의 SPA 리라이트가 이미 /themes/* 를 index.html 로 보낸다.
 */
export type ThemeReturn = 'success' | 'fail' | null

export function themeReturnEntry(): ThemeReturn {
  if (typeof window === 'undefined') return null
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (path === '/themes/success') return 'success'
  if (path === '/themes/fail') return 'fail'
  return null
}

/**
 * Toss 가 테마 결제 성공 URL 에 실어 주는 값들.
 *
 * ⚠️ amount 는 **주소창에 있는 값**이다. 승인 기준이 아니라 대조용이다 —
 * 실제 승인은 서버가 보관한 주문 금액으로 한다.
 */
export function readThemeReturnParams(search: string): {
  paymentKey: string | null
  orderId: string | null
  amount: number | null
  code: string | null
  message: string | null
} {
  const p = new URLSearchParams(search)
  const rawAmount = p.get('amount')
  const amount = rawAmount == null ? null : Number.parseInt(rawAmount, 10)
  return {
    paymentKey: p.get('paymentKey'),
    orderId: p.get('orderId'),
    amount: Number.isFinite(amount as number) ? (amount as number) : null,
    // 실패 리다이렉트는 code/message 를 준다.
    code: p.get('code'),
    message: p.get('message'),
  }
}

/**
 * 물리 주문 결제 복귀 경로.
 *
 * /billing/*(구독), /themes/*(테마)와 **경로를 나눈다.** 세 결제는 확인
 * 엔드포인트도 결과 화면도 다르다. 경로를 공유하면 실물 결제가 구독 confirm 을
 * 타게 되고, "실물 주문은 구독을 건드리지 않는다"는 계약이 깨진다.
 */
export type OrderReturn = 'success' | 'fail' | null

export function orderReturnEntry(): OrderReturn {
  if (typeof window === 'undefined') return null
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (path === '/orders/success') return 'success'
  if (path === '/orders/fail') return 'fail'
  return null
}


/**
 * 크레딧 팩 결제 복귀 경로.
 *
 * 테마(/themes/*)·구독(/billing/*)·실물(/orders/*)과 **경로를 나눈다.** 네 흐름은
 * 확인 엔드포인트도 결과 화면도 다르다. 경로를 공유하면 크레딧 결제가 테마
 * confirm 을 타게 되고, 그건 잘못된 주문을 승인하려는 시도가 된다.
 *
 * vercel.json 의 SPA 리라이트가 이미 /credits/* 를 index.html 로 보낸다.
 */
export type CreditsReturn = 'success' | 'fail' | null

export function creditsReturnEntry(): CreditsReturn {
  if (typeof window === 'undefined') return null
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  if (path === '/credits/success') return 'success'
  if (path === '/credits/fail') return 'fail'
  return null
}

/**
 * Toss 가 크레딧 결제 성공 URL 에 실어 주는 값들.
 *
 * ⚠️ amount 는 **주소창에 있는 값**이다. 승인 기준이 아니라 대조용이다 —
 * 실제 승인은 서버가 보관한 주문 금액으로 한다.
 */
export function readCreditsReturnParams(search: string): {
  paymentKey: string | null
  orderId: string | null
  amount: number | null
  code: string | null
  message: string | null
} {
  const p = new URLSearchParams(search)
  const rawAmount = p.get('amount')
  const amount = rawAmount == null ? null : Number.parseInt(rawAmount, 10)
  return {
    paymentKey: p.get('paymentKey'),
    orderId: p.get('orderId'),
    amount: Number.isFinite(amount as number) ? (amount as number) : null,
    code: p.get('code'),
    message: p.get('message'),
  }
}
