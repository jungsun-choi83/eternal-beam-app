/**
 * 가져온 Soul Trace 편지를 **방금 만들어진 canonical 펫**에 붙인다.
 *
 *   /soul-trace/import → claim (letter_id 저장)
 *        ↓  사진 업로드 → BREATHING → 펫 등록(canonical petId 확정)
 *   여기 → POST /api/v1/orders/letter/link-pet { letter_id, pet_id }
 *        ↓
 *   결제 화면이 "이 펫의 편지"를 정확히 찾는다
 *
 * ── 왜 별도 모듈인가 ────────────────────────────────────────────────────────
 * 서버에는 /letter/link-pet 이 처음부터 있었다. **부르는 쪽이 없었을 뿐이다.**
 * 그래서 soul_trace_letters.pet_id 는 언제나 NULL 이었고, 결제 화면의
 * "이 펫의 편지" 조회는 100% 실패해 아무 편지나 집는 폴백으로 떨어졌다.
 * 빠져 있던 것은 이 한 번의 호출이다.
 *
 * ── 이 모듈이 하지 않는 것 ──────────────────────────────────────────────────
 * 편지를 만들지 않는다. 펫을 만들지 않는다. 실패해도 흐름을 막지 않는다 —
 * 링크는 보조 경로이고, 실패하면 결제 화면이 "편지를 먼저 연결해 주세요"로
 * 막아 준다(잘못된 편지를 인쇄하는 것보다 낫다).
 */

import { OrderApiError, linkLetterToPet } from "./orders-api.ts";
import { getPremiumAccessToken } from "./premium-auth-token.ts";
import {
  clearActiveSoulTraceLetter,
  readActiveSoulTraceLetter,
} from "./soul-trace-handoff.ts";

export type LetterLinkResult =
  | { state: "LINKED"; letterId: string; petId: string }
  /** 붙일 편지가 없다 — Soul Trace 를 거치지 않은 평범한 사용자다(정상). */
  | { state: "NO_PENDING_LETTER" }
  /** 이 펫은 편지를 클레임하기 **전에** 만들어졌다 — 이 편지의 주인이 아니다. */
  | { state: "NOT_THIS_PET"; reason: "pet-predates-claim" }
  | { state: "PENDING_AUTH" }
  | { state: "FAILED"; code?: string; message: string };

/** 주입 지점. pet-registry-api 의 PetRegistrationDeps 와 같은 모양을 유지한다. */
export interface LetterLinkDeps {
  readPending?: typeof readActiveSoulTraceLetter;
  clearPending?: typeof clearActiveSoulTraceLetter;
  getToken?: typeof getPremiumAccessToken;
  link?: typeof linkLetterToPet;
}

/** 클레임 시점의 content_id 를 읽는다. pet-identity 와 **같은 키**를 본다. */
function currentContentId(explicit?: string | null): string {
  const fromArg = (explicit || "").trim();
  if (fromArg) return fromArg;
  try {
    return (localStorage.getItem("eternal_beam_content_id") || "").trim();
  } catch {
    return "";
  }
}

/**
 * 대기 중인 편지가 있으면 이 펫에 붙인다. **멱등이고 조용하다.**
 *
 * 성공하면 표식을 버린다 — 1회용이다. 남겨 두면 그 다음 업로드에도 같은 편지가
 * 따라붙어, 펫 두 마리가 한 편지를 나눠 갖는 상태가 된다.
 */
export async function linkPendingSoulTraceLetter(
  params: { petId: string; contentId?: string | null },
  deps: LetterLinkDeps = {}
): Promise<LetterLinkResult> {
  const petId = (params.petId || "").trim();
  if (!petId) return { state: "NO_PENDING_LETTER" };

  const readPending = deps.readPending ?? readActiveSoulTraceLetter;
  const clearPending = deps.clearPending ?? clearActiveSoulTraceLetter;
  const getToken = deps.getToken ?? getPremiumAccessToken;
  const link = deps.link ?? linkLetterToPet;

  const pending = readPending();
  if (!pending) return { state: "NO_PENDING_LETTER" };

  // 클레임 이후에 만들어진 펫인가. content_id 가 그대로면 예전 펫을 다시 열어
  // 본 것뿐이고, 그 펫에 새 편지를 붙이면 **엉뚱한 펫이 남의 편지를 갖는다.**
  const contentId = currentContentId(params.contentId);
  if (contentId && contentId === pending.contentIdAtClaim) {
    return { state: "NOT_THIS_PET", reason: "pet-predates-claim" };
  }

  const auth = await getToken();
  // 세션 복원 전이면 기다린다. 호출부가 SIGNED_IN 에서 다시 부른다.
  if (!auth.token) return { state: "PENDING_AUTH" };

  try {
    const r = await link({
      letterId: pending.letterId,
      petId,
      accessToken: auth.token,
    });
    clearPending();
    return { state: "LINKED", letterId: r.letterId, petId };
  } catch (e) {
    const code = e instanceof OrderApiError ? e.code : undefined;
    const message = e instanceof Error ? e.message : String(e);
    // 편지가 없거나 내 것이 아니다 — 재시도해도 같은 답이다. 표식을 버려야
    // 이후 모든 업로드가 죽은 편지를 붙이려고 계속 서버를 때리지 않는다.
    if (code === "LETTER_NOT_FOUND") clearPending();
    console.warn("[soul-trace] letter→pet link failed", { petId, code, message });
    return { state: "FAILED", code, message };
  }
}
