/**
 * Phase 9 — My Library 새로고침 재개.
 *
 * 여기 적히는 것은 "화면이 어디 있었는가"라는 가벼운 포인터일 뿐이다(step·
 * petId·motionId·themeId). 실제 펫/모션은 저장하지 않는다 — 새로고침 뒤에도
 * 언제나 서버 레지스트리를 다시 조회해 **지금도 있는지** 확인한 뒤에만
 * 되살린다. 포인터가 가리키는 펫이 더 이상 없거나 그 모션이 더 이상 발행돼
 * 있지 않으면 조용히 앞 단계로 물러난다 — 없는 자산을 있는 것처럼 보여주는
 * 것보다 낫다.
 *
 * sessionStorage 를 쓴다: 탭 하나의 방문 동안만 의미 있는 UI 위치이지,
 * 결제·생성처럼 durable 하게 지킬 대상이 아니다.
 */

export type LibraryFlowStep = "library" | "themes" | "preview";

export type LibraryFlowState = {
  step: LibraryFlowStep;
  petId: string;
  motionId: string | null;
  themeId: number | null;
};

const LIBRARY_FLOW_KEY = "eternal_beam_library_flow_v1";
const STEPS: readonly LibraryFlowStep[] = ["library", "themes", "preview"];

function isStep(value: unknown): value is LibraryFlowStep {
  return typeof value === "string" && (STEPS as readonly string[]).includes(value);
}

export function saveLibraryFlowState(state: LibraryFlowState): void {
  try {
    if (!state.petId) {
      sessionStorage.removeItem(LIBRARY_FLOW_KEY);
      return;
    }
    sessionStorage.setItem(LIBRARY_FLOW_KEY, JSON.stringify(state));
  } catch {
    /* 용량 초과·프라이빗 모드 — 복원이 안 될 뿐, 화면은 그대로 동작한다 */
  }
}

export function readLibraryFlowState(): LibraryFlowState | null {
  try {
    const raw = sessionStorage.getItem(LIBRARY_FLOW_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const candidate = parsed as Partial<LibraryFlowState>;
    if (!isStep(candidate.step)) return null;
    const petId = typeof candidate.petId === "string" ? candidate.petId.trim() : "";
    if (!petId) return null;
    const motionId = typeof candidate.motionId === "string" ? candidate.motionId : null;
    const themeId =
      typeof candidate.themeId === "number" && Number.isFinite(candidate.themeId)
        ? candidate.themeId
        : null;
    return { step: candidate.step, petId, motionId, themeId };
  } catch {
    return null;
  }
}

export function clearLibraryFlowState(): void {
  try {
    sessionStorage.removeItem(LIBRARY_FLOW_KEY);
  } catch {
    /* ignore */
  }
}

export function hasLibraryFlowMarker(): boolean {
  return readLibraryFlowState() != null;
}

export type LibraryPetLookup = {
  petId: string;
};

export type LibraryMotionLookup = {
  motionId: string;
};

export type LibraryFlowRestoreResult =
  /** 되살릴 표식이 없다 — 그리드(library 단계)로. */
  | { status: "none" }
  /** 레지스트리/모션 조회가 아직 끝나지 않았다 — 호출부는 기다렸다가 다시 부른다. */
  | { status: "pending" }
  /** 표식의 펫·모션을 지금은 확인할 수 없다 — 그리드로 안전하게 물러난다(표식도 지운다). */
  | { status: "restore-library" }
  /** 펫·모션·(필요하면) 테마까지 지금도 유효하다 — 그대로 되살린다. */
  | {
      status: "restore";
      petId: string;
      motionId: string | null;
      step: LibraryFlowStep;
      themeId: number | null;
    };

/**
 * 저장된 표식을 **지금** 서버가 돌려준 레지스트리와 대조해서만 신뢰한다.
 *
 * 브라우징(library 단계)은 표식이 없어도 되는 단일 그리드라 복원할 것이
 * 애초에 없다 — 이 함수가 관여하는 것은 그 그리드에서 한 번 모션을 확정해
 * themes/preview 로 넘어간 뒤의 새로고침뿐이다.
 *
 * - petIds 가 null 이면 레지스트리 조회가 아직 안 끝난 것 — "pending".
 * - motionsForPet 이 null 이면(해당 펫의 조회가 아직 안 끝남) 마찬가지로 "pending".
 */
export function resolveLibraryFlowRestore(
  saved: LibraryFlowState | null,
  petIds: readonly LibraryPetLookup[] | null,
  motionsForPet: readonly LibraryMotionLookup[] | null
): LibraryFlowRestoreResult {
  if (!saved || saved.step === "library") return { status: "none" };
  if (petIds === null) return { status: "pending" };

  const pet = petIds.find((p) => p.petId === saved.petId);
  if (!pet) return { status: "restore-library" };

  if (motionsForPet === null) return { status: "pending" };

  const motion = saved.motionId ? motionsForPet.find((m) => m.motionId === saved.motionId) : null;
  if (!motion) return { status: "restore-library" };

  return { status: "restore", petId: pet.petId, motionId: motion.motionId, step: saved.step, themeId: saved.themeId };
}
