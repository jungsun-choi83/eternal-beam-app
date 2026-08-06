/**
 * VideoGenerationService — Luma 기반 "아이들(Idle)" 홀로그램 애니메이션 5종 세트 생성.
 *
 * 파이프라인 (사진 업로드 → 완료까지 자동 연결):
 *   1) 누끼 — 1회만.
 *      - VITE_CLIENT_CUTOUT=1: 브라우저(WASM, @imgly/background-removal)에서 처리 (서버 미호출)
 *      - 그 외(기본): SAM2 누끼 API (POST /api/matting/cutout, videoProcessingApi.cutoutImage)
 *   2) Luma 영상 생성 API (POST /api/generate-idle-variant) — IDLE_TEMPLATE_ORDER 순서대로 5회 순차 호출
 *      (누끼는 이미 끝났으므로 skipPreprocessing=true로 재사용)
 *
 * Constraint 1 (일관성): 아래 5개 모션 프롬프트는 고정값. 절대 수정하지 말 것.
 *   backend/services/luma_idle_templates.py 의 IDLE_TEMPLATES와 항상 동일한 문자열을 유지한다.
 * Constraint 2 (기술 품질): 모든 프롬프트에 TECHNICAL_QUALITY_SUFFIX가 포함되어야 함
 *   → 실제 최종 프롬프트 조립은 서버(luma_idle_templates.build_idle_variant_prompt)에서
 *     이 문구를 항상 덧붙이므로, 여기서는 참고/표시용으로만 상수를 노출한다.
 * Constraint 3 (초점/안전): 배경 환경 생성 금지, 강아지 동작에만 집중 — 서버가 강제.
 *
 * 에러 방지:
 *   - Luma 응답은 서버가 완료될 때까지 폴링(async/await)한 뒤 video_url을 반환한다.
 *   - video_url 확장자가 .mp4인지 클라이언트에서도 재검증한다(서버도 검증 + 재시도함).
 *   - 배경이 순정 블랙이 아니면 서버가 자동으로 보강 프롬프트로 재시도(기본 최대 2회)하고,
 *     그 결과(is_black_background/retries_used)를 그대로 넘겨준다.
 */

import {
  assertUsableCutout,
  cutoutImage,
  getVideoApiBaseUrl,
  type CutoutResult,
} from './videoProcessingApi'
import { clientCutoutFromFile, dataUrlToFile } from '@/lib/client-cutout'

/**
 * 서버 누끼(/api/matting/cutout, /api/cutout)는 Render 512MB 플랜에서 OOM으로
 * 죽는 사례가 확인되어(ai-processing-screen.tsx와 동일 이슈), VITE_CLIENT_CUTOUT=1이면
 * 이 5종 테스트 패널도 브라우저(WASM) 누끼로 전환해 서버를 건드리지 않음.
 * .trim() — Vercel 등 대시보드 값에 공백/개행이 섞여도 안전하게 비교.
 */
const CLIENT_CUTOUT_FIRST = String(import.meta.env.VITE_CLIENT_CUTOUT ?? '').trim() === '1'

/** Constraint 1 — 5개 고정 아이들(Idle) 모션 템플릿. 순서·문구 변경 금지. */
const IDLE_COMMON =
  "Camera angle completely fixed, identical to the original photo's exact angle and framing. Subject's body position, head orientation, and pose must remain unchanged. No rotation, no turning, no shifting of body or head position. Same fur pattern, same lighting, same background. Only the specifically described micro-movement below is allowed — everything else must stay perfectly still."

const IDLE_COMMON_HEAD =
  "Camera angle completely fixed, identical to the original photo's exact angle and framing. Subject's body position and pose must remain unchanged. No body rotation, no turning of the torso, no shifting of body position. Same fur pattern, same lighting, same background. Head rotation is allowed only as specifically described below — everything else must stay perfectly still."

export const IDLE_PROMPT_TEMPLATES = {
  IDLE_BREATH:
    `${IDLE_COMMON} Only the chest and rib area rises and falls very subtly, as if breathing calmly. The movement is barely visible — no more than a few millimeters of expansion. Head, legs, tail, and camera angle do not move at all.`,
  IDLE_HEAD_TILT:
    `${IDLE_COMMON} Only the head tilts gently to one side, as if curiously reacting to a sound, then returns to center. The tilt angle should be small (10-15 degrees max). Body, legs, and torso remain completely still.`,
  IDLE_TAIL_WAG:
    `${IDLE_COMMON} Only the tail wags gently side to side. The rest of the body — head, torso, legs, and overall pose — stays completely static and unchanged.`,
  IDLE_EAR_FLICK:
    `${IDLE_COMMON} Only the ears flick or twitch slightly, as if reacting to a small sound nearby. Head position, body, and camera angle remain completely fixed.`,
  IDLE_LOOK_AROUND:
    `${IDLE_COMMON_HEAD} The head turns slowly to glance left, then right, then returns exactly to the original starting angle by the end of the clip. Body and legs remain still. The final frame must match the first frame's angle precisely, so the loop can restart seamlessly.`,
} as const

export type IdleTemplateKey = keyof typeof IDLE_PROMPT_TEMPLATES

/** 항상 호출되는 순서 — "순차적으로 적용" 요구사항 그대로 고정. */
export const IDLE_TEMPLATE_ORDER: IdleTemplateKey[] = [
  'IDLE_BREATH',
  'IDLE_HEAD_TILT',
  'IDLE_TAIL_WAG',
  'IDLE_EAR_FLICK',
  'IDLE_LOOK_AROUND',
]

/** Constraint 2 — 모든 프롬프트에 필수로 포함되는 기술 품질 문구(참고용, 실제 강제는 서버에서). */
export const TECHNICAL_QUALITY_SUFFIX =
  'black background, high resolution, 4k, cinematic, realistic fur texture, clean edges'

async function safeJson(res: Response): Promise<Record<string, unknown>> {
  try {
    return (await res.json()) as Record<string, unknown>
  } catch {
    return {}
  }
}

function formatErrorDetail(err: Record<string, unknown>, fallback: string): string {
  const d = err.detail
  if (typeof d === 'string') return d
  if (Array.isArray(d)) return d.map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ')
  return fallback
}

function isLikelyMp4Url(url: string): boolean {
  const path = url.split('?')[0].split('#')[0]
  return path.toLowerCase().endsWith('.mp4')
}

/** 브라우저(WASM) 누끼 — data URL 결과를 CutoutResult 형태로 감싸서 이하 로직 재사용. */
async function runClientCutout(
  photo: File,
  contentId?: string
): Promise<{ cutout: CutoutResult; cutoutFile: File }> {
  const dataUrl = await clientCutoutFromFile(photo)
  const cutoutFile = dataUrlToFile(dataUrl, 'cutout.png')
  const base64 = dataUrl.split(',')[1] ?? ''
  const cutout: CutoutResult = {
    content_id: contentId || `idle_client_${Date.now()}`,
    cutout_url: null,
    cutout_png_base64: base64,
  }
  return { cutout, cutoutFile }
}

/** SAM2 누끼 결과(URL 또는 base64)를 Luma 업로드용 File로 변환.
 *
 * 실패한 누끼를 빈 File로 만들어 업로드/생성으로 흘려보내지 않도록, 변환 전에
 * 항상 게이트를 통과시킨다 (subject_detected / error / 이미지 존재 여부).
 */
async function cutoutResultToFile(result: CutoutResult): Promise<File> {
  assertUsableCutout(result)
  if (result.cutout_url) {
    const res = await fetch(result.cutout_url)
    if (!res.ok) throw new Error(`누끼 이미지를 불러오지 못했습니다 (${res.status}).`)
    const blob = await res.blob()
    const type = blob.type?.startsWith('image/') ? blob.type : 'image/png'
    return new File([blob], 'cutout.png', { type })
  }
  if (result.cutout_png_base64) {
    const bytes = Uint8Array.from(atob(result.cutout_png_base64), (c) => c.charCodeAt(0))
    return new File([bytes], 'cutout.png', { type: 'image/png' })
  }
  throw new Error('누끼 결과에 이미지가 없습니다.')
}

export interface IdleVariantResult {
  success: boolean
  content_id: string
  dog_only_nobg_url: string
  template_key: IdleTemplateKey
  video_url: string
  is_mp4: boolean
  is_black_background: boolean
  background_luminance: number | null
  retries_used: number
  loop_meta?: Record<string, unknown>
  prompt: string
  validation?: Record<string, unknown> | null
  validation_history?: Record<string, unknown>[]
}

export interface GenerateIdleVariantOptions {
  userId?: string
  contentId?: string
  /** true(기본) — file이 이미 SAM2 누끼 결과. false면 서버가 SAM2로 다시 누끼. */
  skipPreprocessing?: boolean
  /** 배경이 검정이 아니거나 mp4가 아닐 때 서버가 재시도할 최대 횟수 (기본 2). */
  maxRetries?: number
  /** 개별 fetch 상한(ms). Luma 생성 1건은 수 분 걸릴 수 있음. */
  timeoutMs?: number
}

/**
 * Luma 영상 생성 API 1건 호출 (템플릿 1개).
 * 서버가 완료될 때까지 폴링(await)하고, video_url이 .mp4인지 재확인한다.
 */
export async function generateIdleVariant(
  file: File,
  templateKey: IdleTemplateKey,
  options: GenerateIdleVariantOptions = {}
): Promise<IdleVariantResult> {
  if (!(templateKey in IDLE_PROMPT_TEMPLATES)) {
    throw new Error(`Unknown idle template key: ${templateKey}`)
  }

  const form = new FormData()
  form.append('file', file)
  form.append('template_key', templateKey)
  form.append('user_id', options.userId ?? 'anonymous')
  if (options.contentId) form.append('content_id', options.contentId)
  form.append('skip_preprocessing', String(options.skipPreprocessing !== false))
  form.append('max_retries', String(options.maxRetries ?? 2))

  const ctrl = new AbortController()
  // 서버는 max_retries(기본 2, 즉 최대 3회 시도) × LUMA_POLL_MAX_SEC(기본 1200초)까지
  // 한 요청 안에서 재시도할 수 있음. 예전 10분 상한은 이보다 훨씬 짧아서, 정상적으로
  // 재시도 중인데도 프론트가 먼저 중단(abort)해버려 "멈춘 것처럼" 보이는 원인이 됐음.
  // 20분으로 늘려 서버 쪽 재시도가 끝날 시간을 충분히 준다(그래도 서버보다는 짧게 유지).
  const timeoutMs = options.timeoutMs ?? 20 * 60 * 1000
  const tid = setTimeout(() => ctrl.abort(), timeoutMs)

  let res: Response
  try {
    res = await fetch(`${getVideoApiBaseUrl()}/api/generate-idle-variant`, {
      method: 'POST',
      body: form,
      signal: ctrl.signal,
    })
  } catch (e) {
    clearTimeout(tid)
    if (e instanceof Error && e.name === 'AbortError') {
      throw new Error(`Luma 아이들(${templateKey}) 생성 시간 초과.`)
    }
    throw e
  }
  clearTimeout(tid)

  if (!res.ok) {
    const err = await safeJson(res)
    throw new Error(formatErrorDetail(err, `generate-idle-variant(${templateKey}) failed`))
  }

  const data = (await res.json()) as IdleVariantResult

  // 클라이언트 측 재검증 — 서버도 검증하지만 방어적으로 한 번 더 확인.
  if (!data.video_url || !isLikelyMp4Url(data.video_url)) {
    throw new Error(`서버가 mp4가 아닌 video_url을 반환했습니다: ${data.video_url}`)
  }

  return data
}

export interface GenerateIdleSetOptions {
  userId?: string
  contentId?: string
  maxRetries?: number
  /** 5개 중 일부만 생성하려면 지정 (기본: IDLE_TEMPLATE_ORDER 전체). */
  templates?: IdleTemplateKey[]
  /** 템플릿 1건이 끝날 때마다 호출 (progress UI용). */
  onVariantComplete?: (result: IdleVariantResult, index: number, total: number) => void
  /** 템플릿 1건 시작 시 호출. */
  onVariantStart?: (templateKey: IdleTemplateKey, index: number, total: number) => void
  /**
   * 템플릿 1건이 실패했을 때 호출 (progress UI용).
   * 이 콜백이 없으면 UI가 "running" 상태에 멈춘 것처럼 보일 수 있으니
   * 진행 상태를 그리는 화면은 반드시 이 콜백도 처리해야 함.
   */
  onVariantError?: (templateKey: IdleTemplateKey, message: string, index: number, total: number) => void
  /** SAM2 누끼가 끝났을 때 호출 (미리보기 표시용). */
  onCutoutComplete?: (cutout: CutoutResult) => void
  /** 특정 템플릿 실패 시 전체를 중단할지(기본 false — 나머지는 계속 진행). */
  stopOnError?: boolean
}

export interface IdleVariantOutcome {
  templateKey: IdleTemplateKey
  result?: IdleVariantResult
  error?: string
}

export interface GenerateIdleSetResult {
  contentId: string
  cutout: CutoutResult
  variants: IdleVariantOutcome[]
}

/**
 * 전체 파이프라인: 사진 1장 업로드 → SAM2 누끼(1회) → Luma 아이들 5종을
 * IDLE_TEMPLATE_ORDER 순서로 순차 호출.
 *
 * 한 템플릿이 실패해도(stopOnError=false, 기본) 나머지 템플릿은 계속 시도하고,
 * 최종적으로 성공/실패가 섞인 variants 배열을 반환한다.
 */
export async function generateIdleAnimationSet(
  photo: File,
  options: GenerateIdleSetOptions = {}
): Promise<GenerateIdleSetResult> {
  // 1) 누끼 — 한 번만. VITE_CLIENT_CUTOUT=1이면 브라우저(WASM)에서, 아니면 서버(SAM2)에서.
  let cutout: CutoutResult
  let cutoutFile: File
  if (CLIENT_CUTOUT_FIRST) {
    const clientResult = await runClientCutout(photo, options.contentId)
    cutout = clientResult.cutout
    cutoutFile = clientResult.cutoutFile
  } else {
    cutout = await cutoutImage(photo, {
      userId: options.userId,
      contentId: options.contentId,
      saveToStorage: false,
    })
    // cutoutImage()가 이미 assertUsableCutout()을 통과시키지만, 이 헬퍼가 다른
    // 경로에서 호출될 수도 있으므로 변환 직전에 한 번 더 확인한다.
    cutoutFile = await cutoutResultToFile(cutout)
  }
  options.onCutoutComplete?.(cutout)

  const contentId = cutout.content_id || options.contentId || `idle_${Date.now()}`

  // 2) Luma 아이들 5종 — 순차 호출 (동시에 여러 개 쏘지 않음: 요청사항 "순차적으로 적용").
  const templates = options.templates ?? IDLE_TEMPLATE_ORDER
  const outcomes: IdleVariantOutcome[] = []

  for (let i = 0; i < templates.length; i++) {
    const templateKey = templates[i]
    options.onVariantStart?.(templateKey, i, templates.length)
    try {
      const result = await generateIdleVariant(cutoutFile, templateKey, {
        userId: options.userId,
        contentId,
        skipPreprocessing: true, // 누끼는 위에서 이미 끝났음
        maxRetries: options.maxRetries,
      })
      outcomes.push({ templateKey, result })
      options.onVariantComplete?.(result, i, templates.length)
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      outcomes.push({ templateKey, error: message })
      options.onVariantError?.(templateKey, message, i, templates.length)
      if (options.stopOnError) break
    }
  }

  return { contentId, cutout, variants: outcomes }
}
