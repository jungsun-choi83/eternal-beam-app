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

// ── Device D1 — Phase 7 BREATHING 전용 전송 본문 ────────────────────────────

export type Phase7PetReadyRequest = {
  contentId: string
  petId: string
  /** D1 은 BREATHING 만 허용한다 — 다중 모션 매니페스트는 D2 의 명시 작업이다. */
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
 * Phase 7 BREATHING → /demo/pet-ready 본문 (검증 포함, 순수 함수).
 *
 * 검증 규칙 (Device D1):
 *   * pet_id 는 같은 Phase 7B 펫이어야 한다 — 결정론 규칙 pet_id == `pet_{content_id}`
 *     (pet_reference_service.pet_id_for_content 와 동일). 어긋나면 남의/옛 펫의
 *     영상이 기기로 나간다.
 *   * motion_id 는 정확히 BREATHING — D1 범위 밖 모션은 거절한다.
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
  if (motionId !== 'BREATHING') return { ok: false, reason: 'unsupported_motion' }
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
