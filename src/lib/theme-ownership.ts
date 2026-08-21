/**
 * 테마 소유권 **순수 모델** — 카드 한 장이 무엇을 보여 줄지 정한다.
 *
 *   Basic Theme     FREE        [Use]
 *   Beach           NOT OWNED   [Buy]
 *   Snow Forest     NOT OWNED   [Buy]
 *   (구매 후) Beach  OWNED       [Use]
 *
 * 여기에 네트워크도 React 도 없다. behavior-library.ts 가 Behavior Library 에
 * 대해 하는 일과 같은 역할이다 — 판정을 순수 함수로 모아 테스트로 덮는다.
 *
 * ── 서버가 권위다 ────────────────────────────────────────────────────────────
 * 프론트가 소유 여부를 **다시 계산하지 않는다.** themes.ts 의 `premium` 플래그로
 * 판정하면 서버 설정(THEME_PAID_KEYS)과 어긋나고, 그 순간 눌러도 402 가 나는
 * 버튼이 생긴다. 카탈로그 응답이 없을 때만 안전한 폴백을 쓴다.
 *
 * ── 구독과 무관하다 ──────────────────────────────────────────────────────────
 * 이 파일에는 멤버십·구독이라는 말이 나오지 않는다. 테마 소유권은 다른 축이다.
 */

import type { MemorialTheme } from "@/components/memorial/themes";

/** 서버 카탈로그 한 줄 (theme-store-api 가 파싱한 모양). */
export interface ThemeOffer {
  themeKey: string;
  free: boolean;
  owned: boolean;
  priceKrw: number | null;
  purchasable: boolean;
}

/** 카드가 그릴 상태. */
export type ThemeOwnershipState =
  /** 무료 — 언제나 쓸 수 있다. */
  | "free"
  /** 유료이고 보유 중. */
  | "owned"
  /** 유료이고 미보유 — 살 수 있다. */
  | "not-owned"
  /** 유료인데 가격이 아직 없다 — 살 수 없다(PM 미정). */
  | "coming-soon"
  /** 카탈로그를 아직 못 받았다. */
  | "unknown";

/** 카드에 붙일 동작. */
export type ThemeAction = "use" | "buy" | "none";

export interface ThemeRow {
  themeKey: string;
  state: ThemeOwnershipState;
  action: ThemeAction;
  priceKrw: number | null;
  /** 지금 이 테마를 선택(사용)해도 되는가. */
  usable: boolean;
}

/**
 * 카탈로그 응답 → key 로 찾을 수 있는 표.
 *
 * 배열을 그대로 들고 다니면 카드마다 find() 를 돌게 되고, 테마가 늘수록
 * 렌더가 O(n²) 이 된다. 화면이 카드를 많이 그리는 자리라 미리 접어 둔다.
 */
export function indexOffers(offers: ThemeOffer[] | null): Map<string, ThemeOffer> {
  const m = new Map<string, ThemeOffer>();
  for (const o of offers ?? []) {
    if (o?.themeKey) m.set(o.themeKey, o);
  }
  return m;
}

/**
 * 이 테마의 표시 상태.
 *
 * 카탈로그가 없을 때(로딩·오류)의 폴백이 중요하다: **유료 테마를 사용 가능으로
 * 보여 주지 않는다.** 반대로 무료 테마는 폴백에서도 쓸 수 있어야 한다 — 무료는
 * 결제와 무관하고, 카탈로그 장애가 무료 경험을 막으면 안 된다.
 */
export function themeRow(
  theme: Pick<MemorialTheme, "themeKey" | "premium">,
  offers: Map<string, ThemeOffer>
): ThemeRow {
  const offer = offers.get(theme.themeKey);

  if (!offer) {
    // 폴백: themes.ts 의 premium 플래그만으로 판단한다.
    const free = !theme.premium;
    return {
      themeKey: theme.themeKey,
      state: free ? "free" : "unknown",
      action: free ? "use" : "none",
      priceKrw: null,
      usable: free,
    };
  }

  if (offer.free) {
    return {
      themeKey: theme.themeKey,
      state: "free",
      action: "use",
      priceKrw: 0,
      usable: true,
    };
  }

  if (offer.owned) {
    return {
      themeKey: theme.themeKey,
      state: "owned",
      action: "use",
      priceKrw: offer.priceKrw,
      usable: true,
    };
  }

  // 유료 · 미보유. 가격이 없으면 팔 수도 없다.
  const sellable = offer.purchasable && offer.priceKrw != null && offer.priceKrw > 0;
  return {
    themeKey: theme.themeKey,
    state: sellable ? "not-owned" : "coming-soon",
    action: sellable ? "buy" : "none",
    priceKrw: offer.priceKrw,
    usable: false,
  };
}

/**
 * 이 테마를 지금 **선택**해도 되는가.
 *
 * 선택 경로 자체는 예전 그대로다(theme-selection-store / place_id / 기기 동기화).
 * 이 함수는 그 앞에 "살 것을 먼저 사라"는 게이트만 얹는다.
 */
export function canUseTheme(
  theme: Pick<MemorialTheme, "themeKey" | "premium">,
  offers: Map<string, ThemeOffer>
): boolean {
  return themeRow(theme, offers).usable;
}

/** 표시용 가격. 가격이 없으면 null — 화면이 "준비 중"으로 그린다. */
export function formatPriceKrw(price: number | null): string | null {
  if (price == null || price <= 0) return null;
  return `₩${price.toLocaleString("ko-KR")}`;
}

/**
 * 구매 후 카탈로그를 다시 받기 전까지 쓸 낙관적 갱신.
 *
 * 서버 응답으로 수렴하지만, 결제 직후 화면이 잠깐 NOT OWNED 로 남아 있으면
 * "돈은 나갔는데 안 샀다"로 보인다 — 결제 화면에서 가장 불안한 순간이다.
 */
export function markOwned(
  offers: Map<string, ThemeOffer>,
  themeKey: string
): Map<string, ThemeOffer> {
  const next = new Map(offers);
  const cur = next.get(themeKey);
  if (cur) next.set(themeKey, { ...cur, owned: true, purchasable: false });
  return next;
}
