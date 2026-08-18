/**
 * 실제 업로드된 펫의 신원(pet_id) — 하나의 출처.
 *
 * 왜 필요한가: idle(System A)과 COME_CLOSER(System B)가 서로 다른 신원을 쓰고
 * 있었다. idle 은 저장 경로에 user_id 를 쓰는데 프론트가 그 값을 아예 넘기지
 * 않아 'anonymous' 로 저장됐고, COME_CLOSER 조회는 getEternalBeamUserId() 를
 * 썼다. 게다가 pet_id 는 크레딧 세션 경로에서만 기록돼 순수 웹 플로우에서는
 * 항상 비어 있었고, 그러면 백엔드가 `<user_id>_pet` 으로 조회한다
 * (generated_motions_service.default_pet_id).
 *
 * ── 불변식 ────────────────────────────────────────────────────────────────
 *   현재 content_id = X  →  pet_id = pet_X
 *
 * 처음 구현은 "기록된 pet_id 가 있으면 무조건 그것을 쓴다"였는데, 그게 버그였다.
 * eternal_beam_pet_id 는 **전역**이라 사진을 새로 올려 content_id 가 Y 로 바뀌어도
 * 예전 pet_X 가 그대로 남아 조회키를 오염시켰다. 그 결과 새 펫의 COME_CLOSER 를
 * 영영 찾지 못하고, 사람이 매번 localStorage 를 지워야 했다.
 *
 * 그래서 pet_id 를 **그것이 속한 content_id 와 함께** 저장한다. 현재 content 에
 * 실제로 묶여 있음이 증명된 값만 파생값을 이긴다. 묶이지 않은 잔여 전역값은
 * content_id 를 아는 순간 무시된다.
 *
 * 권위 있는 출처(크레딧 세션이 서버에서 받아 온 pet_id)는 그대로 존중하되,
 * setAuthoritativePetId() 로 현재 content 에 묶어서 기록한다.
 */

/** 레거시 키 — credit-pipeline.ts 가 직접 읽으므로 계속 채워 준다. */
const PET_ID_KEY = "eternal_beam_pet_id";
/** pet_id ↔ content_id 결속 기록. 이것이 있어야 "권위 있음"으로 인정한다. */
const PET_BINDING_KEY = "eternal_beam_pet_binding";
const CONTENT_ID_KEY = "eternal_beam_content_id";

type PetBinding = { petId: string; contentId: string };

/** content_id → pet_id. 순수 함수라 백엔드 제출과 프론트 조회가 같은 값을 얻는다. */
export function derivePetIdFromContent(contentId: string): string {
  return `pet_${contentId.trim()}`;
}

function readBinding(): PetBinding | null {
  try {
    const raw = localStorage.getItem(PET_BINDING_KEY);
    if (!raw) return null;
    const b = JSON.parse(raw) as Partial<PetBinding>;
    const petId = (b.petId || "").trim();
    const contentId = (b.contentId || "").trim();
    return petId && contentId ? { petId, contentId } : null;
  } catch {
    return null; // 손상된 값은 없는 것으로 본다 — 파생으로 복구된다
  }
}

function writeBinding(petId: string, contentId: string): void {
  try {
    localStorage.setItem(PET_BINDING_KEY, JSON.stringify({ petId, contentId }));
    // 레거시 리더(credit-pipeline.ts)가 계속 동작하도록 같이 갱신한다.
    localStorage.setItem(PET_ID_KEY, petId);
  } catch {
    /* quota — 파생값은 순수 함수라 다음에 또 같은 값이 나온다 */
  }
}

function resolveContentId(explicit?: string | null): string {
  const fromArg = (explicit || "").trim();
  if (fromArg) return fromArg;
  try {
    return (localStorage.getItem(CONTENT_ID_KEY) || "").trim();
  } catch {
    return "";
  }
}

/**
 * 서버가 정해 준 pet_id(크레딧 세션 등)를 **현재 content 에 묶어** 기록한다.
 * 이렇게 기록된 값만 이후 조회에서 파생값보다 우선한다.
 */
export function setAuthoritativePetId(petId: string, contentId?: string | null): void {
  if (typeof window === "undefined") return;
  const id = (petId || "").trim();
  if (!id) return;

  const cid = resolveContentId(contentId);
  if (!cid) {
    // 어느 content 의 펫인지 모르면 결속을 만들지 않는다 — 그래야 다음 업로드를
    // 오염시키지 않는다. 레거시 리더를 위해 값 자체는 남긴다.
    try {
      localStorage.setItem(PET_ID_KEY, id);
    } catch {
      /* ignore */
    }
    return;
  }
  writeBinding(id, cid);
}

/**
 * 현재 content 의 pet_id.
 *
 *   1) 현재 content_id 에 묶인 기록이 있으면 그것 (서버가 준 권위 있는 값)
 *   2) 없으면 현재 content_id 에서 파생하고 묶어서 기록
 *   3) content_id 자체를 모르면 레거시 전역값 (유일한 단서), 없으면 null
 *
 * 핵심: content_id 를 아는 한, **묶이지 않은 잔여 전역값은 절대 이기지 못한다.**
 */
export function getEternalBeamPetId(contentId?: string | null): string | null {
  if (typeof window === "undefined") return null;

  const cid = resolveContentId(contentId);
  if (!cid) {
    try {
      return localStorage.getItem(PET_ID_KEY)?.trim() || null;
    } catch {
      return null;
    }
  }

  const bound = readBinding();
  if (bound && bound.contentId === cid) return bound.petId;

  const derived = derivePetIdFromContent(cid);
  writeBinding(derived, cid);
  return derived;
}
