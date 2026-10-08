/**
 * 펫 입력 잠금 (Stage 1c) — 클라이언트 쪽.
 *
 * 제품 규칙: 생성이 시작되기 전에는 사진을 자유롭게 더하고 빼고 바꿀 수 있다.
 * 생성이 시작되면(실행이 만들어지면) 바꿀 수 없다.
 *
 * 잠금의 정본은 서버다 (pet_reference_service.pet_inputs_locked):
 *   - FAILED/CANCELLED 가 아닌 생성 실행이 있거나
 *   - 생성 자산(정본/키프레임/모션)이 있으면 잠김.
 * 서버는 잠긴 펫의 변경 요청에 409 PHASE1_LOCKED 로 답한다.
 *
 * 클라이언트는 두 신호를 쓴다:
 *   1. 서버 답 (GET /api/v1/pet/references/{pet_id} 의 inputs_locked) — 알면 이것이 이긴다.
 *      실패한 실행 뒤 다시 고칠 수 있게 풀리는 것도 서버만 안다.
 *   2. 로컬 마커 (generation-resume 의 "확인을 눌렀다" 기록) — 서버 답을 아직
 *      모를 때, 확인 직후부터 곧바로 잠긴 화면을 보여 주기 위한 것.
 *
 * (순수 모듈 테스트를 위해 상대 경로 import 규칙을 따른다 — original-reference.ts 참고)
 */

export const PHASE1_LOCKED_CODE = "PHASE1_LOCKED";

function apiBase(): string {
  try {
    const raw = (import.meta as { env?: Record<string, string> }).env?.VITE_API_BASE_URL;
    return (raw || "").trim().replace(/\/$/, "");
  } catch {
    return "";
  }
}

/** 서버가 "이 펫은 잠겼다"고 답한 오류인가 (Phase1IntakeError / ReferenceSyncError 공통). */
export function isPhase1LockedError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { code?: unknown }).code === PHASE1_LOCKED_CODE
  );
}

/**
 * 서버에 잠금 상태를 묻는다. 모르면(네트워크·인증·판정 불가) null — throw 하지 않는다.
 */
export async function fetchPetInputsLocked(params: {
  petId: string;
  accessToken: string;
}): Promise<boolean | null> {
  const petId = (params.petId || "").trim();
  const token = (params.accessToken || "").trim();
  if (!petId || !token) return null;
  try {
    const res = await fetch(`${apiBase()}/api/v1/pet/references/${encodeURIComponent(petId)}`, {
      method: "GET",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { inputs_locked?: boolean | null };
    return typeof body.inputs_locked === "boolean" ? body.inputs_locked : null;
  } catch {
    return null;
  }
}

/**
 * 화면이 쓸 잠금 여부. 서버 답을 알면 그것이 정본이고, 모를 때만 로컬 마커를 쓴다.
 *
 * **사진이 없는 자리는 잠기지 않는다.** 새로고침 뒤에는 사진이 복원되지 않아 자리가
 * 비어 돌아오고, 거기에 잠긴 아이의 신원만 남아 있을 수 있다. 그 자리에서 고르는
 * 사진은 새 아이다(phase1-intake-session.identityForAddedPhotos 가 새 신원을
 * 발급한다) — 잠긴 아이를 여는 것이 아니다. 잠긴 아이 자체는 서버가 계속 막는다.
 */
export function resolvePetInputsLocked(signals: {
  serverLocked: boolean | null | undefined;
  localMarker: boolean;
  hasPhotos: boolean;
}): boolean {
  if (!signals.hasPhotos) return false;
  if (typeof signals.serverLocked === "boolean") return signals.serverLocked;
  return signals.localMarker;
}

/**
 * 업로드 화면의 Start/Continue 가 갈 곳.
 *
 * 잠긴 아이는 처리 화면(aiProcessing)으로 보내지 않는다 — 거기서는 서버가
 * PHASE1_LOCKED 로 답할 것이 뻔하다. 이미 시작된 생성을 이어 보는 미리보기로 간다.
 */
export function intakeContinueTarget(state: {
  canStart: boolean;
  photosLocked: boolean;
}): "aiProcessing" | "preview" | null {
  if (!state.canStart) return null;
  return state.photosLocked ? "preview" : "aiProcessing";
}
