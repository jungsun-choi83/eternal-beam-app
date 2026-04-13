/**
 * 영상 처리 API (서버 사이드 렌더링)
 * - 누끼: POST /api/cutout (rembg + Alpha Matting)
 * - 합성: POST /api/compose-video (결제 Gate + FFmpeg) → unique_url, nfc_payload
 */

/** API base (no trailing slash). Dev: '' → same-origin so Vite proxies /api → :8000 */
const getBaseUrl = (): string => {
  const explicit = import.meta.env.VITE_VIDEO_API_URL || import.meta.env.VITE_API_URL
  if (explicit) return String(explicit).replace(/\/$/, '')
  if (import.meta.env.DEV) return ''
  // 배포(프리뷰 포함): localhost 백엔드는 방문자 브라우저에 없음 — 반드시 VITE_VIDEO_API_URL 설정
  return ''
}

/** 프리뷰/정적 URL을 절대 주소로 붙일 때 사용 (내부 로직과 동일). */
export const getVideoApiBaseUrl = getBaseUrl

function requireVideoApiBase(): void {
  const hasExplicit = !!(import.meta.env.VITE_VIDEO_API_URL || import.meta.env.VITE_API_URL)
  if (import.meta.env.PROD && !hasExplicit) {
    throw new Error(
      '배포 사이트에는 영상 API 주소가 필요합니다. Vercel 등에 VITE_VIDEO_API_URL(예: 배포한 Python 서버 https URL)을 설정한 뒤 다시 빌드하세요.'
    )
  }
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

export interface CutoutResult {
  content_id: string
  cutout_url?: string | null
  cutout_png_base64?: string | null
  error?: string
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
 * 사진 업로드 → 서버에서 배경 제거(rembg + Alpha Matting)
 * model: isnet-general-use(강아지/털) | u2net_human_seg(사람)
 */
export async function cutoutImage(
  file: File,
  options: {
    userId?: string
    contentId?: string
    saveToStorage?: boolean
    /** isnet-general-use(강아지·털) | u2net_human_seg(사람) */
    model?: string
  } = {}
): Promise<CutoutResult> {
  requireVideoApiBase()
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('save_to_storage', String(options.saveToStorage !== false))
  if (options.model) form.append('model', options.model)

  let res: Response
  try {
    res = await fetch(`${getBaseUrl()}/api/cutout`, {
      method: 'POST',
      body: form,
    })
  } catch (e) {
    throw wrapNetworkError(
      e,
      '누끼 서버에 연결할 수 없습니다. 새 터미널에서 `npm run video-api`로 백엔드(포트 8000)를 실행한 뒤 다시 시도하세요.',
    )
  }
  if (!res.ok) {
    const err = (await res.json().catch(() => ({}))) as Record<string, unknown>
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
  requireVideoApiBase()
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
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || '영상 합성 요청 실패')
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
  requireVideoApiBase()
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
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || err.message || '프리뷰 생성 실패')
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

/** Pet pipeline: optional YOLO+rembg, then Supabase URL + Luma idle + action (no payment). */
export interface GeneratePetVideoResult {
  success: boolean
  content_id: string
  dog_only_nobg_url: string
  idle_video_url: string
  action_video_url: string
  prompts?: { idle: string; action: string }
}

export async function generatePetVideo(
  file: File,
  options: {
    userId?: string
    contentId?: string
    /** Use true when file is already a cutout (e.g. after /api/cutout). */
    skipPreprocessing?: boolean
  } = {}
): Promise<GeneratePetVideoResult> {
  requireVideoApiBase()
  const form = new FormData()
  form.append('file', file)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('skip_preprocessing', String(options.skipPreprocessing === true))

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
    const err = (await res.json().catch(() => ({}))) as Record<string, unknown>
    throw new Error(formatHttpErrorDetail(err, 'generate-pet-video failed'))
  }
  return res.json()
}
