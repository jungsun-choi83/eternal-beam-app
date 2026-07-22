/**
 * 영상 처리 API (서버 사이드 렌더링)
 * - 누끼: POST /api/matting/cutout (SAM2 박스 프롬프트 + ViTMatte 경계 정제, rembg 대체)
 *   * 예전 파이프라인(rembg)은 POST /api/cutout — VITE_CUTOUT_PIPELINE=rembg로 되돌릴 수 있음
 * - 합성: POST /api/compose-video (결제 Gate + FFmpeg) → unique_url, nfc_payload
 */

import { ensureIdleMp4Url } from '@/lib/device-host-flags'

/** 임시 Cloudflare 터널 URL은 만료되므로 프로덕션에서 무시 → same-origin /api (vercel.json rewrites) */
function normalizeApiBase(raw: string | undefined): string {
  if (!raw) return ''
  const s = String(raw).trim().replace(/\/$/, '')
  if (!s) return ''
  const lower = s.toLowerCase()
  if (lower.includes('trycloudflare.com') || lower.includes('ngrok-free.app') || lower.includes('ngrok.io')) {
    if (import.meta.env.DEV) {
      console.warn('[video-api] 임시 터널 URL은 무시합니다. npm run video-api 또는 VITE_VIDEO_API_URL을 사용하세요.', s)
    }
    return ''
  }
  return s
}

/** Render FastAPI — 프로덕션 기본 (Vercel /api 프록시는 콜드스타트·긴 누끼 중 502) */
export const DEFAULT_RENDER_VIDEO_API = 'https://eternal-beam-video-api.onrender.com'

/** API base (no trailing slash). Dev: '' → same-origin so Vite proxies /api → :8000 */
const getBaseUrl = (): string => {
  const explicit = normalizeApiBase(
    import.meta.env.VITE_VIDEO_API_URL || import.meta.env.VITE_API_URL
  )
  if (explicit) return explicit
  if (import.meta.env.DEV) return ''
  // 배포: 브라우저 → Render 직접 (CORS 허용). Vercel rewrite 타임아웃 502 방지
  return DEFAULT_RENDER_VIDEO_API
}

/** 프리뷰/정적 URL을 절대 주소로 붙일 때 사용 (내부 로직과 동일). */
export const getVideoApiBaseUrl = getBaseUrl

/**
 * 누끼 파이프라인 선택: 기본은 SAM2+ViTMatte(/api/matting/cutout).
 * 문제 생기면 VITE_CUTOUT_PIPELINE=rembg로 예전 /api/cutout(rembg)으로 즉시 되돌릴 수 있게 남겨둠.
 */
function getCutoutPath(): string {
  const pipeline = String(import.meta.env.VITE_CUTOUT_PIPELINE || '').trim().toLowerCase()
  return pipeline === 'rembg' ? '/api/cutout' : '/api/matting/cutout'
}

function validateVideoApiBase(): void {
  if (!import.meta.env.PROD) return
  const raw = import.meta.env.VITE_VIDEO_API_URL || import.meta.env.VITE_API_URL
  const s = raw != null ? String(raw).trim() : ''
  // Empty is allowed: app may call same-origin /api through reverse proxy.
  if (!s) return
  const lower = s.toLowerCase()
  if (lower.includes('localhost') || lower.includes('127.0.0.1')) {
    throw new Error(
      '프로덕션에서는 VITE_VIDEO_API_URL에 localhost를 넣을 수 없습니다. 방문자 PC에는 당신의 로컬 서버가 없습니다. FastAPI를 클라우드에 배포한 뒤 그 https 주소를 넣으세요.'
    )
  }
}

function missingVideoApiConfigMessage(): string {
  return [
    '누끼·합성 API가 설정되지 않았습니다.',
    '- 방법 A(권장): Vercel 환경변수 `VITE_VIDEO_API_URL`에 FastAPI 배포 주소(https://...)를 넣고 재배포',
    '- 방법 B: Vercel에서 `/api/*`를 백엔드로 Rewrite(프록시) 설정',
  ].join('\n')
}

async function safeJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>
  } catch {
    return {}
  }
}

/** 프로덕션/로컬에 맞는 안내 (Failed to fetch 등) */
function cutoutFetchFailedMessage(status?: number): string {
  if (status === 502 || status === 503 || status === 504) {
    return '누끼 서버가 깨어나는 중입니다(502). 1분 뒤 다시 시도해 주세요. Wi‑Fi를 권장합니다.'
  }
  if (import.meta.env.PROD) {
    return [
      '누끼 서버에 연결할 수 없습니다.',
      'Render 무료 플랜은 잠들었다가 첫 요청에 1~2분 걸릴 수 있습니다.',
      'VITE_VIDEO_API_URL이 만료된 터널 URL이면 삭제 후 재배포하세요.',
    ].join(' ')
  }
  return '누끼 서버에 연결할 수 없습니다. 프로젝트 루트에서 `npm run video-api`로 백엔드(포트 8000)를 실행한 뒤 다시 시도하세요.'
}

/** 네트워크/터널 오류 — 데모 모드로 진행 가능 */
export function isCutoutApiUnreachableError(message: string): boolean {
  const m = message.toLowerCase()
  return (
    m.includes('누끼 서버') ||
    m.includes('502') ||
    m.includes('503') ||
    m.includes('504') ||
    m.includes('bad gateway') ||
    m.includes('gateway') ||
    m.includes('api가 설정') ||
    m.includes('failed to fetch') ||
    m.includes('network') ||
    m.includes('err_name_not_resolved') ||
    m.includes('load failed') ||
    m.includes('trycloudflare') ||
    m.includes('name not resolved')
  )
}

function wrapNetworkError(err: unknown, hint: string): Error {
  if (err instanceof TypeError || (err instanceof Error && err.message === 'Failed to fetch')) {
    return new Error(hint)
  }
  return err instanceof Error ? err : new Error(String(err))
}

function formatHttpErrorDetail(err: Record<string, unknown>, fallback: string): string {
  const d = err.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d))
    return d
      .map((x: { msg?: string; type?: string }) => x?.msg || JSON.stringify(x))
      .join(', ')
  if (d && typeof d === 'object' && 'message' in d && typeof (d as { message: string }).message === 'string')
    return (d as { message: string }).message
  if (typeof err.message === 'string' && err.message) return err.message
  return fallback
}

export interface CutoutQualityMeta {
  semi_transparent_ratio?: number
  boundary_pixel_count?: number
  needs_refinement?: boolean
  threshold?: number
  quality_score?: number
  refined?: boolean
  cutout_pass?: string
  refine_error?: string
}

export interface CutoutResult {
  content_id: string
  cutout_url?: string | null
  cutout_png_base64?: string | null
  error?: string
  /** 0~1, 경계 반투명 비율이 낮을수록 높음 */
  quality_score?: number | null
  cutout_quality?: CutoutQualityMeta | null
}

export interface ComposeVideoResult {
  success: boolean
  content_id: string
  unique_url: string
  nfc_payload: {
    version: number
    content_id: string
    unique_url: string
    theme_id: string
    slot_number: number | null
  }
}

/**
 * 사진 업로드 → 서버에서 배경 제거.
 * 기본 파이프라인: SAM2(박스 프롬프트) + ViTMatte 경계 정제 (/api/matting/cutout).
 * VITE_CUTOUT_PIPELINE=rembg 설정 시 예전 rembg 파이프라인(/api/cutout)으로 전환.
 * model/fast/autoRefine 옵션은 rembg 파이프라인에서만 사용됨.
 */
export async function cutoutImage(
  file: File,
  options: {
    userId?: string
    contentId?: string
    saveToStorage?: boolean
    /** isnet-general-use(강아지·털) | u2net_human_seg(사람) */
    model?: string
    /** 알파 매팅 생략만 (auto_refine 끔) */
    fast?: boolean
    /**
     * fast 1차 → 알파 경계 분석 → 장모 추정 시 matting 재처리 (기본 true)
     */
    autoRefine?: boolean
    /** fetch 상한(ms). adaptive 기본 4분 */
    timeoutMs?: number
  } = {}
): Promise<CutoutResult> {
  validateVideoApiBase()
  const cutoutPath = getCutoutPath()
  const isRembgPipeline = cutoutPath === '/api/cutout'
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('save_to_storage', String(options.saveToStorage !== false))
  // options.model은 rembg 모델 이름(예: isnet-general-use)이라 SAM2+ViTMatte
  // 파이프라인(/api/matting/cutout)에는 그대로 넘기면 안 됨 — rembg 경로에서만 전달.
  if (isRembgPipeline && options.model) form.append('model', options.model)
  const autoRefine = options.autoRefine !== false
  if (isRembgPipeline) {
    if (options.fast) form.append('fast', 'true')
    if (autoRefine && !options.fast) {
      form.append('auto_refine', 'true')
    } else if (!autoRefine) {
      form.append('auto_refine', 'false')
    }
  }

  const timeoutMs =
    options.timeoutMs ??
    (options.fast ? 90_000 : autoRefine ? 240_000 : 180_000)
  const ctrl = new AbortController()
  const tid = setTimeout(() => ctrl.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${getBaseUrl()}${cutoutPath}`, {
      method: 'POST',
      body: form,
      signal: ctrl.signal,
    })
  } catch (e) {
    throw wrapNetworkError(e, cutoutFetchFailedMessage())
  } finally {
    clearTimeout(tid)
  }
  if (!res.ok) {
    if (import.meta.env.PROD && getBaseUrl() === '' && res.status === 404) {
      throw new Error(missingVideoApiConfigMessage())
    }
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new Error(cutoutFetchFailedMessage(res.status))
    }
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '배경 제거 요청 실패'))
  }
  return res.json()
}

/**
 * 누끼 PNG + 테마 ID → 서버에서 FFmpeg 합성 (결제 Gate 적용)
 * subject_only=true: 배경 없이 피사체만 (15cm 기기용, Fringe/Halo 방지)
 * payment_status: 유료 테마일 때 true여야 합성 진행
 */
export async function composeVideo(
  cutoutFile: File,
  options: {
    userId?: string
    contentId?: string
    themeId?: string
    paymentStatus?: boolean
    maxHeight?: number
    /** 피사체만 출력 (배경 없음). 강아지만 검은 배경 위에. */
    subjectOnly?: boolean
  }
): Promise<ComposeVideoResult> {
  validateVideoApiBase()
  const form = new FormData()
  form.append('cutout_file', cutoutFile)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('theme_id', options.themeId ?? (options.subjectOnly ? 'subject_only' : ''))
  form.append('payment_status', String(options.paymentStatus === true))
  form.append('max_height', String(options.maxHeight ?? 720))
  form.append('subject_only', String(options.subjectOnly === true))

  const res = await fetch(`${getBaseUrl()}/api/compose-video`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    if (import.meta.env.PROD && getBaseUrl() === '' && res.status === 404) {
      throw new Error(missingVideoApiConfigMessage())
    }
    const err = await safeJson(res)
    if (!('detail' in err)) err.detail = res.statusText
    throw new Error(formatHttpErrorDetail(err, '영상 합성 요청 실패'))
  }
  return res.json()
}

/**
 * 구매한 테마 ID 목록 (서버에서 purchased_slots 조회)
 */
export async function getPurchasedThemes(userId: string = 'anonymous'): Promise<{ theme_ids: string[] }> {
  const res = await fetch(`${getBaseUrl()}/api/purchased-slots?user_id=${encodeURIComponent(userId)}`)
  if (!res.ok) return { theme_ids: [] }
  return res.json()
}

/**
 * 실시간 프리뷰 생성 — Scale/Position 적용
 */
export async function generatePreview(params: {
  background_id: string
  cutoutFile: File
  scale: number
  position_x: number
  position_y: number
}): Promise<{ preview_url: string; preview_id: string }> {
  validateVideoApiBase()
  const form = new FormData()
  form.append('cutout_file', params.cutoutFile)
  form.append('background_id', params.background_id)
  form.append('scale', String(params.scale))
  form.append('position_x', String(params.position_x))
  form.append('position_y', String(params.position_y))

  const res = await fetch(`${getBaseUrl()}/api/preview`, {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    if (import.meta.env.PROD && getBaseUrl() === '' && res.status === 404) {
      throw new Error(missingVideoApiConfigMessage())
    }
    const err = await safeJson(res)
    if (!('detail' in err)) err.detail = res.statusText
    throw new Error(formatHttpErrorDetail(err, '프리뷰 생성 실패'))
  }
  return res.json()
}

/**
 * 최종 합성 및 Content_ID 생성
 */
export async function composeFinal(params: {
  background_id: string
  subject_id: string
  scale: number
  position_x: number
  position_y: number
  user_id?: string
}): Promise<{ content_id: string; nfc_payload: { content_id: string; version: string } }> {
  const res = await fetch(`${getBaseUrl()}/api/compose-final`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...params,
      user_id: params.user_id ?? 'anonymous',
    }),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || '최종 합성 실패')
  }
  return res.json()
}

/**
 * Content_ID로 레이어 메타데이터 조회 (하드웨어용)
 */
export async function getContent(contentId: string): Promise<{
  background_url: string
  subject_url: string
  scale: number
  position_x: number
  position_y: number
}> {
  const res = await fetch(`${getBaseUrl()}/api/content/${encodeURIComponent(contentId)}`)
  if (!res.ok) throw new Error('Content not found')
  return res.json()
}

/**
 * Pet pipeline: optional YOLO+rembg, then Supabase URL + Luma idle (+ action, no payment).
 * 기본은 idle 전용(idleOnly=true) — 20종 액션은 Live Portrait가 맡을 예정이라 Luma 액션
 * 영상은 더 생성하지 않음. action_video_url은 idleOnly일 때 null.
 */
export interface GeneratePetVideoResult {
  success: boolean
  content_id: string
  dog_only_nobg_url: string
  idle_video_url: string
  action_video_url: string | null
  prompts?: { idle: string; action?: string }
}

export async function generatePetVideo(
  file: File,
  options: {
    userId?: string
    contentId?: string
    /** Use true when file is already a cutout (e.g. after /api/cutout). */
    skipPreprocessing?: boolean
    /** 아이들(미세 모션)만 생성 — 기본 true. false면 예전처럼 액션도 함께 생성. */
    idleOnly?: boolean
  } = {}
): Promise<GeneratePetVideoResult> {
  validateVideoApiBase()
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('skip_preprocessing', String(options.skipPreprocessing === true))
  form.append('idle_only', String(options.idleOnly !== false))

  const ctrl = new AbortController()
  const PET_VIDEO_TIMEOUT_MS = 25 * 60 * 1000
  const tid = setTimeout(() => ctrl.abort(), PET_VIDEO_TIMEOUT_MS)
  let res: Response
  try {
    res = await fetch(`${getBaseUrl()}/api/generate-pet-video`, {
      method: 'POST',
      body: form,
      signal: ctrl.signal,
    })
  } catch (e) {
    clearTimeout(tid)
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error(
        '서버 응답 시간 초과(25분). Luma 생성이 지연됐거나 백엔드 로그를 확인하세요.'
      )
    }
    throw e
  }
  clearTimeout(tid)
  if (!res.ok) {
    if (import.meta.env.PROD && getBaseUrl() === '' && res.status === 404) {
      throw new Error(missingVideoApiConfigMessage())
    }
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      // 주의: 이 메시지에 "generate-pet-video" 문자열을 넣지 말 것 —
      // isSkippableLumaError()가 그 문자열을 포함한 오류는 데모 모드로 스킵 처리함.
      throw new Error(`Pet video server error (HTTP ${res.status})`)
    }
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, 'generate-pet-video failed'))
  }
  return res.json()
}

/** API idle URL 또는 same-origin 데모 mp4 — 항상 재생 가능한 mp4 반환 */
export function resolveIdleVideoUrl(apiUrl: string | null | undefined): string {
  return ensureIdleMp4Url(apiUrl)
}

/** 구독 크레딧 지갑 */
export interface WalletBalance {
  user_id: string
  current_credits: number
}

export async function getWalletBalance(userId: string): Promise<WalletBalance> {
  validateVideoApiBase()
  const res = await fetch(
    `${getBaseUrl()}/api/v1/pet/wallet/${encodeURIComponent(userId)}`
  )
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '지갑 조회 실패'))
  }
  return res.json()
}

/** 장소 1곳 × IDLE/TOUCH/VOICE/NFC — 크레딧 4개 차감 후 Luma 비동기 제출 */
export interface GenerateWithCreditResult {
  session_id: string
  user_id: string
  pet_id: string
  place_id: string
  credits_charged: number
  credits_remaining: number
  submitted: number
  submit_errors: Array<{ action_id?: string; ok?: boolean; error?: string }>
  status: string
  webhook_path: string
}

export async function generateWithCredit(body: {
  user_id: string
  pet_image_url: string
  selected_place_id: string
  pet_id?: string
}): Promise<GenerateWithCreditResult> {
  validateVideoApiBase()
  const ctrl = new AbortController()
  const tid = setTimeout(() => ctrl.abort(), 20_000)
  let res: Response
  try {
    res = await fetch(`${getBaseUrl()}/api/v1/pet/generate-with-credit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
  } finally {
    clearTimeout(tid)
  }
  if (res.status === 403) {
    const err = await safeJson(res)
    throw new Error(
      typeof err.detail === 'string'
        ? err.detail
        : '크레딧이 부족합니다. 구독 플랜을 업그레이드하세요.'
    )
  }
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '영상 생성(크레딧) 요청 실패'))
  }
  return res.json() as Promise<GenerateWithCreditResult>
}

/** IAP 단품: credit_pack_4 (4,900 KRW → +4 credits) */
export interface VerifyAndChargeResult {
  success: boolean
  user_id: string
  product_id: string
  amount_krw: number
  credits_added: number
  credits_remaining: number
  payment_id?: number | null
  transaction_id?: string | null
  store_type: 'apple' | 'google'
  status: string
  idempotent_replay: boolean
  message: string
}

export async function verifyAndChargeIAP(body: {
  user_id: string
  receipt_data: string
  store_type: 'apple' | 'google'
  product_id?: string
}): Promise<VerifyAndChargeResult> {
  validateVideoApiBase()
  const res = await fetch(`${getBaseUrl()}/api/v1/payment/verify-and-charge`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      product_id: 'credit_pack_4',
      ...body,
    }),
  })
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '결제 검증·충전 실패'))
  }
  return res.json()
}

/** Standard 구독 상태 (Unity·앱) */
export interface SubscriptionStatusResult {
  user_id: string
  plan_id?: string | null
  status?: 'active' | 'canceled' | 'expired' | null
  next_billing_date?: string | null
  entitled: boolean
  credits_remaining?: number | null
  display_name?: string | null
  price_krw_monthly?: number | null
  credits_per_month?: number | null
}

export async function getSubscriptionStatus(
  userId: string
): Promise<SubscriptionStatusResult> {
  validateVideoApiBase()
  const res = await fetch(
    `${getBaseUrl()}/api/v1/subscription/status/${encodeURIComponent(userId)}`
  )
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '구독 상태 조회 실패'))
  }
  return res.json()
}

/** Apple/Google/목업 구독 웹훅 */
export interface SubscriptionWebhookResult {
  success: boolean
  user_id: string
  plan_id: string
  event_type: string
  subscription_status: 'active' | 'canceled' | 'expired'
  credits_added: number
  credits_remaining?: number | null
  next_billing_date?: string | null
  entitled: boolean
  idempotent_replay: boolean
  message: string
}

export async function postSubscriptionWebhook(body: {
  store_type?: 'apple' | 'google' | 'mock'
  notification_type: string
  user_id: string
  plan_id?: string
  transaction_id?: string
  product_id?: string
  raw?: Record<string, unknown>
}): Promise<SubscriptionWebhookResult> {
  validateVideoApiBase()
  const res = await fetch(`${getBaseUrl()}/api/v1/subscription/webhook`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '구독 웹훅 처리 실패'))
  }
  return res.json()
}
