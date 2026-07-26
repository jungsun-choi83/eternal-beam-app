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

/** 호출용 이름 목록 — 별명 + ~야 변형 포함 */
export function buildWakeNames(raw: string): string[] {
  const base = parseWakeNames(raw);
  const out: string[] = [];
  const seen = new Set<string>();

  const push = (name: string) => {
    const n = name.trim();
    if (!n || seen.has(n)) return;
    seen.add(n);
    out.push(n);
  };

  for (const name of base) {
    push(name);
    if (name.length >= 2 && !name.endsWith("야") && !name.endsWith("아")) {
      push(`${name}야`);
    }
  }
  return out;
}

export function getWakeNames(): string[] {
  return buildWakeNames(getPetName());
}

/** Pi 마이크 voice UDP에 반려 이름 전달 */
export async function syncPetProfileToDevice(): Promise<boolean> {
  const names = getWakeNames();
  if (!names.length) return false;
  const { syncPetWakeNamesToPi } = await import("@/lib/pi-sensor-bridge");
  return syncPetWakeNamesToPi(names, names[0]);
}
