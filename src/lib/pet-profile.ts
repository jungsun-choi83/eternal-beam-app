/** 반려 이름 — 기기 이름 호출 반응용 */

const PET_NAME_KEY = "eternal_beam_pet_name";

export function getPetName(): string {
  if (typeof window === "undefined") return "";
  return localStorage.getItem(PET_NAME_KEY)?.trim() ?? "";
}

export function setPetName(name: string): void {
  const trimmed = name.trim();
  if (!trimmed) {
    localStorage.removeItem(PET_NAME_KEY);
    return;
  }
  localStorage.setItem(PET_NAME_KEY, trimmed);
}

export function parseWakeNames(raw: string): string[] {
  return raw
    .split(/[,，、/|]/)
    .map((part) => part.trim())
    .filter((part) => part.length >= 1);
}
