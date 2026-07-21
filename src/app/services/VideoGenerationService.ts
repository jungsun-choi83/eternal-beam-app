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

import { cutoutImage, getVideoApiBaseUrl, type CutoutResult } from './videoProcessingApi'
import { clientCutoutFromFile, dataUrlToFile } from '@/lib/client-cutout'

/**
 * 서버 누끼(/api/matting/cutout, /api/cutout)는 Render 512MB 플랜에서 OOM으로
 * 죽는 사례가 확인되어(ai-processing-screen.tsx와 동일 이슈), VITE_CLIENT_CUTOUT=1이면
 * 이 5종 테스트 패널도 브라우저(WASM) 누끼로 전환해 서버를 건드리지 않음.
 * .trim() — Vercel 등 대시보드 값에 공백/개행이 섞여도 안전하게 비교.
 */
const CLIENT_CUTOUT_FIRST = String(import.meta.env.VITE_CLIENT_CUTOUT ?? '').trim() === '1'

/** Constraint 1 — 5개 고정 아이들(Idle) 모션 템플릿. 순서·문구 변경 금지. */
export const IDLE_PROMPT_TEMPLATES = {
  IDLE_BREATH:
    'A realistic dog sitting still, breathing softly, chest gently moving up and down, subtle focus on fur, black background, 4k, cinematic, high quality.',
  IDLE_HEAD_TILT:
    'A dog looking at the viewer, head tilting slightly from left to right, curious expression, soft fur motion, black background, 4k, realistic.',
  IDLE_TAIL_WAG:
    'A dog standing calmly, tail wagging slowly and gently, happy and attentive, cinematic lighting, black background, 4k.',
  IDLE_EAR_FLICK:
    'A dog sitting, ears flicking slightly, alert and cute, sharp focus on eyes, black background, 4k, high resolution.',
  IDLE_LOOK_AROUND:
    'A dog looking around slowly, subtle eye movement, calm and peaceful, realistic fur, black background, 4k, cinematic.',
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

/** SAM2 누끼 결과(URL 또는 base64)를 Luma 업로드용 File로 변환. */
async function cutoutResultToFile(result: CutoutResult): Promise<File> {
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
  const timeoutMs = options.timeoutMs ?? 10 * 60 * 1000 // Luma 1건 폴링 상한 (서버 LUMA_POLL_MAX_SEC와 별개 안전장치)
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
    if (cutout.error) {
      throw new Error(`SAM2 누끼 실패: ${cutout.error}`)
    }
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
      if (options.stopOnError) break
    }
  }

  return { contentId, cutout, variants: outcomes }
}
