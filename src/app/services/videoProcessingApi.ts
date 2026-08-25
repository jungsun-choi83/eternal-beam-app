/**
 * 영상 처리 API (서버 사이드 렌더링)
 * - 누끼: POST /api/matting/cutout (SAM2 박스 프롬프트 + ViTMatte 경계 정제, rembg 대체)
 *   * 예전 파이프라인(rembg)은 POST /api/cutout — VITE_CUTOUT_PIPELINE=rembg로 되돌릴 수 있음
 * - 합성: POST /api/compose-video (결제 Gate + FFmpeg) → unique_url, nfc_payload
 */

import { ensureIdleMp4Url } from '@/lib/device-host-flags'
import { traceImage } from '@/lib/image-trace' // [IMAGE-TRACE]

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

/**
 * 누끼 실패 코드 — 백엔드 backend/services/cutout_errors.py 와 1:1 대응.
 * "이 사진으로는 안 된다"는 뜻이라, 서버가 죽은 것과 달리 클라이언트 폴백으로
 * 우회하면 안 되고 사용자에게 알려야 한다.
 */
export const CUTOUT_REJECTION_CODES = [
  'SUBJECT_NOT_DETECTED',
  'CUTOUT_MASK_TOO_SMALL',
  'CUTOUT_MASK_TOO_LARGE',
  'CUTOUT_ALPHA_EMPTY',
  'CUTOUT_RECTANGLE_LIKE',
] as const

export type CutoutRejectionCode = (typeof CUTOUT_REJECTION_CODES)[number]

/** 누끼 파이프라인이 사진 자체를 거절한 경우 (HTTP 422). */
export class CutoutRejectedError extends Error {
  readonly code: CutoutRejectionCode | string
  readonly status: number
  readonly diagnostics?: CutoutDiagnostics

  constructor(
    code: string,
    message: string,
    status: number,
    diagnostics?: CutoutDiagnostics
  ) {
    super(message)
    this.name = 'CutoutRejectedError'
    this.code = code
    this.status = status
    this.diagnostics = diagnostics
  }
}

export function isCutoutRejectedError(err: unknown): err is CutoutRejectedError {
  return err instanceof CutoutRejectedError
}

/** 서버가 항상 채워 주는 진단 필드 (backend vitmatte_service.Diagnostics). */
export interface CutoutDiagnostics {
  detector?: string
  detector_model?: string | null
  subject_detected?: boolean
  subject_class?: string | null
  detection_confidence?: number | null
  raw_bbox?: number[] | null
  sam2_prompt_bbox?: number[] | null
  crop_bbox?: number[] | null
  segmenter_requested?: string | null
  segmenter_used?: string | null
  segmenter_fallback?: boolean
  fallback_reason?: string | null
  segmenter_error?: string | null
  sam2_score?: number | null
  mask_area_fraction?: number | null
  mask_bbox_fill_ratio?: number | null
  rectangle_like_mask?: boolean
  alpha_area_fraction?: number | null
  input_width?: number | null
  input_height?: number | null
  processing_width?: number | null
  processing_height?: number | null
}

export interface CutoutQualityMeta extends CutoutDiagnostics {
  semi_transparent_ratio?: number
  boundary_pixel_count?: number
  needs_refinement?: boolean
  threshold?: number
  quality_score?: number
  refined?: boolean
  /** 'vitmatte' | 'rembg_alpha_matting' | null — 실제로 돌아간 정제 종류 */
  refinement_type?: string | null
  /** rembg adaptive 경로에서 2차 매팅 패스가 실제로 돌았는지 */
  second_pass?: boolean
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
  /** 서버가 실제로 지원 동물을 검출했는지 (Luma 진행 전 게이트) */
  subject_detected?: boolean
  cutout_quality?: CutoutQualityMeta | null
}

/** 서버 응답이 실제로 쓸 수 있는 누끼인지 — Luma로 넘기기 전 공통 게이트 */
export function assertUsableCutout(result: CutoutResult): void {
  if (result.error) {
    throw new Error(result.error)
  }
  if (result.subject_detected === false) {
    throw new CutoutRejectedError(
      'SUBJECT_NOT_DETECTED',
      '사진에서 반려동물을 찾지 못했습니다.',
      422,
      result.cutout_quality ?? undefined
    )
  }
  if (!result.cutout_url && !result.cutout_png_base64) {
    throw new Error('누끼 결과에 이미지가 없습니다.')
  }
}

/** 422 구조화 detail을 CutoutRejectedError로 변환 (아니면 null) */
function parseCutoutRejection(
  status: number,
  body: Record<string, unknown>
): CutoutRejectedError | null {
  if (status !== 422) return null
  const d = body.detail
  if (!d || typeof d !== 'object' || Array.isArray(d)) return null
  const detail = d as Record<string, unknown>
  const code = typeof detail.code === 'string' ? detail.code : ''
  if (!code) return null
  const message =
    typeof detail.message === 'string' ? detail.message : '이 사진으로는 처리할 수 없습니다.'
  const diagnostics =
    detail.diagnostics && typeof detail.diagnostics === 'object'
      ? (detail.diagnostics as CutoutDiagnostics)
      : undefined
  return new CutoutRejectedError(code, message, status, diagnostics)
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
  // [IMAGE-TRACE] fetch() 직전 — 이 크기가 백엔드 input_width/input_height 가 된다.
  await traceImage('upload:POST ' + cutoutPath, file, 'unknown', `base=${getBaseUrl() || '(same-origin)'}`)

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
    // 422 = 사진 자체가 거절됨 (피사체 미검출 등). 서버 장애와 구분해야 하므로
    // 전용 오류 타입으로 던진다 — 호출부가 클라이언트 폴백으로 우회하면 안 됨.
    const rejected = parseCutoutRejection(res.status, err)
    if (rejected) throw rejected
    throw new Error(formatHttpErrorDetail(err, '배경 제거 요청 실패'))
  }
  const result = (await res.json()) as CutoutResult
  // 레거시 호환: 예전 백엔드는 실패해도 200 + {"error": ...} 를 돌려줬다.
  assertUsableCutout(result)
  return result
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
  /** COME_CLOSER (웹 전용 프리미엄 액션). 아직 없으면 null. */
  come_closer_video_url?: string | null
  idle_validation?: Record<string, unknown> | null
  idle_validation_history?: Record<string, unknown>[]
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
    /**
     * 정본 장면 필드 (canonical-scene.sceneFormFields).
     * 있으면 프로바이더가 이 장면에서 출발하고 배경이 구워진 영상이 나온다.
     * 없으면 예전 경로 — 백엔드가 누끼를 단색 판에 눌러 붙인다.
     */
    scene?: Record<string, string>
  } = {}
): Promise<GeneratePetVideoResult> {
  validateVideoApiBase()
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('skip_preprocessing', String(options.skipPreprocessing === true))
  form.append('idle_only', String(options.idleOnly !== false))
  // 장면 필드는 이름 그대로 실어 보낸다 — 서버 폼 파라미터와 1:1 이라
  // 중간에 이름을 바꾸는 지점이 없다(어긋나면 조용히 레거시로 떨어진다).
  if (options.scene) {
    for (const [k, v] of Object.entries(options.scene)) {
      if (v) form.append(k, v)
    }
  }

  // [IMAGE-TRACE] Luma 생성으로 넘어가는 누끼 파일의 실제 해상도.
  await traceImage('upload:POST /api/generate-pet-video', file, 'cutout-result')

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
    const err = await safeJson(res)

    // ── 제출 **전** 거절은 프로바이더 실패가 아니다 (Phase 20) ─────────────
    // 멱등성 저장소 불가(503)·이미 진행 중(409)은 돈이 나가지 않은 상태다.
    // 코드를 잃어버리면 화면이 "생성 실패"로 뭉뚱그리고, 고객은 다시 눌러
    // 유료 제출을 반복한다. 그래서 코드를 error 객체에 실어 보낸다.
    //
    // ⚠️ 이 검사는 502/503/504 일반 처리보다 **먼저** 와야 한다 — 그렇지 않으면
    //    503(IDEMPOTENCY_UNAVAILABLE)이 "서버 오류"로 삼켜진다.
    // **알려진 제출 전 코드만** 가로챈다. 넓게 잡으면 422 누끼 거절
    // (CutoutRejectedError — 진단 정보와 전용 UI 가 있다)까지 삼켜 평범한
    // Error 로 바꿔 버린다.
    const PRE_SUBMISSION = ['GENERATION_IDEMPOTENCY_UNAVAILABLE', 'GENERATION_IN_PROGRESS']
    const code = String(
      (err as { detail?: { code?: string } })?.detail?.code ?? ''
    ).trim()
    if (code && PRE_SUBMISSION.includes(code)) {
      const e = new Error(
        String((err as { detail?: { message?: string } })?.detail?.message ?? code)
      ) as Error & { code?: string; status?: number }
      e.code = code
      e.status = res.status
      throw e
    }

    if (res.status === 502 || res.status === 503 || res.status === 504) {
      // 주의: 이 메시지에 "generate-pet-video" 문자열을 넣지 말 것 —
      // isSkippableLumaError()가 그 문자열을 포함한 오류는 데모 모드로 스킵 처리함.
      throw new Error(`Pet video server error (HTTP ${res.status})`)
    }
    // 서버가 생성 직전에 누끼를 거절한 경우 — 과금 전에 멈춘 것이므로 그대로 노출.
    const rejected = parseCutoutRejection(res.status, err)
    if (rejected) throw rejected
    throw new Error(formatHttpErrorDetail(err, 'generate-pet-video failed'))
  }
  return res.json()
}

/** API idle URL — 표시용; cutout 있어도 Goya idle 목업 폴백 허용 */
export function resolveIdleVideoUrl(
  apiUrl: string | null | undefined,
  cutoutUrl?: string | null
): string {
  return ensureIdleMp4Url(apiUrl, { cutoutUrl });
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

/**
 * **본인** 구독 상태. 신원은 서버가 토큰에서 확정한다 — user_id 를 보내지 않는다.
 *
 * 예전에는 경로에 user_id 를 넣어 인증 없이 불렀다. 그 값은 localStorage 에서
 * 온 문자열이라, 프리미엄 인가가 보는 신원과 어긋나면 결제한 사용자가 "구독 없음"
 * 으로 읽혔다. 이제 두 경로가 같은 신원을 쓴다.
 */
export async function getSubscriptionStatus(
  accessToken: string
): Promise<SubscriptionStatusResult> {
  validateVideoApiBase()
  const res = await fetch(`${getBaseUrl()}/api/v1/subscription/status`, {
    cache: 'no-store',
    headers: { Authorization: `Bearer ${accessToken}` },
  })
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

/**
 * 목업 구독 웹훅 — **인증 필수**.
 *
 * 서버가 바디의 user_id 를 무시하고 토큰에서 신원을 확정한다. 그래서 여기서
 * user_id 를 보내지 않는다. 실제 스토어 웹훅(apple/google)은 프론트가 부르는
 * 경로가 아니다 — 공유 시크릿으로 스토어만 호출한다.
 */
export async function postSubscriptionWebhook(body: {
  notification_type: string
  plan_id?: string
  transaction_id?: string
  product_id?: string
}, accessToken: string): Promise<SubscriptionWebhookResult> {
  validateVideoApiBase()
  const res = await fetch(`${getBaseUrl()}/api/v1/subscription/webhook`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${accessToken}`,
    },
    body: JSON.stringify({ ...body, store_type: 'mock' }),
  })
  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatHttpErrorDetail(err, '구독 웹훅 처리 실패'))
  }
  return res.json()
}
