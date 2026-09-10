/**
 * /demo/pet-ready 요청 본문 조립 — 의존성 없는 순수 모듈.
 *
 * pi-sensor-bridge.ts 에서 분리한 이유: 그쪽은 `@/` 별칭 import 가 있어
 * node:test 에서 그대로 불러올 수 없다. 전송 계약만 따로 떼어 두면
 * 네트워크·브라우저 없이 검증할 수 있다.
 */

export type PetReadyRequest = {
  contentId: string
  idleUrl?: string | null
  cutoutUrl?: string | null
  /**
   * packed vstack(RGB+매트 한 파일) URL — 있으면 S23 이 단일 디코더 모드로 재생한다.
   * **추가** 필드다: idle_url 을 절대 대체하지 않는다. packed 를 모르는 기존 S23
   * 빌드가 지금까지처럼 video_url/idle_url 을 읽어 휘도 키 모드로 동작해야 한다.
   */
  packedUrl?: string | null
}

/** 값이 비었거나 공백뿐이면 키 자체를 만들지 않는다 (Pi 쪽 화이트리스트와 동일 규칙). */
export function buildPetReadyBody(payload: PetReadyRequest): Record<string, string> {
  const body: Record<string, string> = { content_id: payload.contentId.trim() }
  const idleUrl = payload.idleUrl?.trim()
  const cutoutUrl = payload.cutoutUrl?.trim()
  const packedUrl = payload.packedUrl?.trim()
  if (idleUrl) body.idle_url = idleUrl
  if (cutoutUrl) body.cutout_url = cutoutUrl
  if (packedUrl) body.packed_url = packedUrl
  return body
}

// ── Device D1/M5-lite — Phase 7 모션 전송 본문 ──────────────────────────────

/**
 * 기기(S23)로 실어 보낼 수 있는 모션 — M5-lite Phase 1 집합.
 *
 * D1 은 BREATHING 하나였다. Phase 1 은 상호작용 3종을 **프리로드 대상**으로
 * 넓힌다 — 재생 지시가 아니다: 기기는 motion_id 별로 저장만 하고, BREATHING
 * 만 홈 루프로 튼다 (센서 트리거 재생은 Phase 2 의 명시 작업이다).
 */
export const DEVICE_PRELOAD_MOTIONS = [
  'BREATHING',
  'PET_HEAD',
  'LOOK_UP',
  'COME_CLOSER',
] as const
export type DevicePreloadMotionId = (typeof DEVICE_PRELOAD_MOTIONS)[number]

export type Phase7PetReadyRequest = {
  contentId: string
  petId: string
  /** DEVICE_PRELOAD_MOTIONS 중 하나 — 그 밖의 모션은 거절한다. */
  motionId: string
  /** 재생 리졸버가 준 **호출 시점 서명** URL. 저장된/하드코딩 URL 금지. */
  packedUrl: string
  /** 명시 전달 포맷 — 파일명 추정에 앞선다. */
  deliveryFormat: string | null
  cutoutUrl?: string | null
}

export type Phase7PetReadyResult =
  | { ok: true; body: Record<string, string> }
  | { ok: false; reason: 'identity_mismatch' | 'unsupported_motion' | 'missing_url' }

/**
 * Phase 7 모션 → /demo/pet-ready 본문 (검증 포함, 순수 함수).
 *
 * 검증 규칙 (Device D1 → M5-lite Phase 1):
 *   * pet_id 는 같은 Phase 7B 펫이어야 한다 — 결정론 규칙 pet_id == `pet_{content_id}`
 *     (pet_reference_service.pet_id_for_content 와 동일). 어긋나면 남의/옛 펫의
 *     영상이 기기로 나간다.
 *   * motion_id 는 DEVICE_PRELOAD_MOTIONS 중 하나 — 그 밖은 거절한다.
 *   * packed URL 필수 — 없는데 보내면 구형 키만 남아 packed 검증이 불가능하다.
 *
 * 구형 S23 빌드 호환: idle_url/video_url 에도 같은 URL 을 실어 packed 를 모르는
 * 빌드가 기존 휘도 키 경로로나마 재생하게 한다(파일명 `_packed.mp4` 를 아는
 * 빌드는 packed_url 을 우선한다). delivery_format 은 명시 필드로 함께 간다.
 */
export function buildPhase7PetReadyBody(req: Phase7PetReadyRequest): Phase7PetReadyResult {
  const contentId = req.contentId.trim()
  const petId = req.petId.trim()
  const motionId = req.motionId.trim().toUpperCase()
  const packedUrl = req.packedUrl?.trim()
  if (!contentId || !petId || petId !== `pet_${contentId}`) {
    return { ok: false, reason: 'identity_mismatch' }
  }
  if (!(DEVICE_PRELOAD_MOTIONS as readonly string[]).includes(motionId)) {
    return { ok: false, reason: 'unsupported_motion' }
  }
  if (!packedUrl) return { ok: false, reason: 'missing_url' }

  const body = buildPetReadyBody({
    contentId,
    idleUrl: packedUrl,
    cutoutUrl: req.cutoutUrl,
    packedUrl,
  })
  body.pet_id = petId
  body.motion_id = motionId
  if (req.deliveryFormat?.trim()) body.delivery_format = req.deliveryFormat.trim()
  return { ok: true, body }
}

// ── Device M5-lite Phase 1 — 다중 모션 프리로드 세트 ────────────────────────

export type DeviceMotionPreloadRequest = {
  contentId: string
  petId: string
  cutoutUrl?: string | null
  /** motion_id → READY 자산. URL 없는 항목은 조용히 빠진다 (없는 모션은 없는 채로). */
  motions: Array<{
    motionId: string
    packedUrl?: string | null
    deliveryFormat?: string | null
  }>
}

export type DeviceMotionPreloadBody = {
  motionId: DevicePreloadMotionId
  body: Record<string, string>
}

/**
 * READY 모션 세트 → /demo/pet-ready 본문 목록 (전송 순서 포함, 순수 함수).
 *
 * 순서 계약: **BREATHING 이 항상 마지막이다.** 단일 슬롯 구형 S23 빌드는
 * 마지막으로 받은 URL 을 트는데, 홈 루프는 BREATHING 이어야 한다. motion_id
 * 저장을 아는 새 빌드는 순서와 무관하다 (BREATHING 외에는 저장만 한다).
 *
 * 각 본문은 buildPhase7PetReadyBody 의 검증을 그대로 거친다 — URL 없음,
 * 미허용 모션, 신원 불일치는 그 항목만 빠진다. 같은 모션이 여러 번 오면
 * 마지막 항목이 이긴다.
 */
export function buildDeviceMotionPreloadBodies(
  req: DeviceMotionPreloadRequest,
): DeviceMotionPreloadBody[] {
  const byMotion = new Map<string, { packedUrl?: string | null; deliveryFormat?: string | null }>()
  for (const m of req.motions) {
    byMotion.set(m.motionId.trim().toUpperCase(), m)
  }
  const ordered: readonly DevicePreloadMotionId[] = [
    ...DEVICE_PRELOAD_MOTIONS.filter((id) => id !== 'BREATHING'),
    'BREATHING',
  ]
  const out: DeviceMotionPreloadBody[] = []
  for (const motionId of ordered) {
    const m = byMotion.get(motionId)
    if (!m) continue
    const built = buildPhase7PetReadyBody({
      contentId: req.contentId,
      petId: req.petId,
      motionId,
      packedUrl: m.packedUrl ?? '',
      deliveryFormat: m.deliveryFormat ?? null,
      cutoutUrl: req.cutoutUrl,
    })
    if (built.ok) out.push({ motionId, body: built.body })
  }
  return out
}
