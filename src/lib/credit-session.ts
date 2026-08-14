import type { GenerateWithCreditResult } from "@/app/services/videoProcessingApi";
import { setAuthoritativePetId } from "@/lib/pet-identity";

export const ETERNAL_BEAM_CREDIT_KEY = "eternal_beam_credit_v1";

export interface StoredCreditSession {
  session_id: string;
  user_id: string;
  pet_id: string;
  place_id: string;
  credits_remaining: number;
  credits_charged: number;
  submitted: number;
  status: string;
  pet_image_url: string;
}

export function saveCreditSession(
  result: GenerateWithCreditResult,
  petImageUrl: string
): void {
  const stored: StoredCreditSession = {
    session_id: result.session_id,
    user_id: result.user_id,
    pet_id: result.pet_id,
    place_id: result.place_id,
    credits_remaining: result.credits_remaining,
    credits_charged: result.credits_charged,
    submitted: result.submitted,
    status: result.status,
    pet_image_url: petImageUrl,
  };
  try {
    sessionStorage.setItem(ETERNAL_BEAM_CREDIT_KEY, JSON.stringify(stored));
    // 서버가 준 pet_id 는 권위 있다 — 다만 **현재 content 에 묶어서** 기록해야
    // 다음 업로드(새 content_id)의 조회키를 오염시키지 않는다.
    setAuthoritativePetId(result.pet_id);
    localStorage.setItem("eternal_beam_place_id", result.place_id);
  } catch {
    /* ignore quota */
  }
}

export function loadCreditSession(): StoredCreditSession | null {
  try {
    const raw = sessionStorage.getItem(ETERNAL_BEAM_CREDIT_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as StoredCreditSession;
  } catch {
    return null;
  }
}
