/**
 * 프리미엄 테마를 화면이 그릴 그룹으로 나눈다 (Phase 5 — Theme Selection redesign).
 *
 * **판정은 새로 하지 않는다.** themeRow(theme-ownership.ts)가 이미 정한 소유
 * 상태를 그대로 읽어 카드를 어느 섹션에 놓을지만 정한다:
 *
 *   Included(무료)  — 여기서 다루지 않는다. freeMemorialThemes 를 그대로 쓴다.
 *   Owned           — 유료이고 서버 카탈로그가 owned 로 표시.
 *   Premium(잠김)   — 유료이고 아직 보유하지 않음(구매 가능/준비 중 모두 포함).
 *   Custom          — requiresGeneration 테마. 소유 여부와 무관하게 항상 여기다 —
 *                      생성 흐름을 먼저 거쳐야 하는 갈래이지 "산 것"의 의미가 다르다.
 */

import { themeRow, type ThemeOffer } from "./theme-ownership.ts";
import type { MemorialTheme } from "../components/memorial/themes.ts";

export interface ThemeGroups {
  ownedThemes: MemorialTheme[];
  lockedThemes: MemorialTheme[];
  customThemes: MemorialTheme[];
}

export function groupPremiumThemes(
  premiumThemes: MemorialTheme[],
  offers: Map<string, ThemeOffer>
): ThemeGroups {
  const ownedThemes: MemorialTheme[] = [];
  const lockedThemes: MemorialTheme[] = [];
  const customThemes: MemorialTheme[] = [];

  for (const theme of premiumThemes) {
    if (theme.requiresGeneration) {
      customThemes.push(theme);
      continue;
    }
    if (themeRow(theme, offers).state === "owned") {
      ownedThemes.push(theme);
    } else {
      lockedThemes.push(theme);
    }
  }

  return { ownedThemes, lockedThemes, customThemes };
}
