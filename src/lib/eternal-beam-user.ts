/** 앱 전역 user_id — 지갑·Luma 크레딧 세션에 사용 */

const USER_ID_KEY = "eternal_beam_user_id";
const ANON_ID_KEY = "eternal_beam_anon_id";

export function getEternalBeamUserId(displayName?: string | null): string {
  if (typeof window === "undefined") return "anonymous";

  const stored = localStorage.getItem(USER_ID_KEY)?.trim();
  if (stored) return stored;

  const fromName = displayName?.trim().replace(/\s+/g, "_").toLowerCase();
  if (fromName && fromName.length >= 2) {
    localStorage.setItem(USER_ID_KEY, fromName);
    return fromName;
  }

  let anon = localStorage.getItem(ANON_ID_KEY)?.trim();
  if (!anon) {
    anon = `user_${Date.now().toString(36)}`;
    localStorage.setItem(ANON_ID_KEY, anon);
  }
  return anon;
}

export function setEternalBeamUserId(userId: string): void {
  const id = userId.trim();
  if (!id) return;
  localStorage.setItem(USER_ID_KEY, id);
}
