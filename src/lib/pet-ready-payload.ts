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
