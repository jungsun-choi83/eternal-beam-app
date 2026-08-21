/**
 * 테마 소유권 표시 모델.
 *
 *   Basic Theme  FREE       [Use]
 *   Beach        NOT OWNED  [Buy]
 *   (구매 후)     OWNED      [Use]
 *
 * 여기서 지키는 것: **프론트가 소유 여부를 다시 계산하지 않는다.** 서버 카탈로그가
 * 권위이고, 없을 때만 안전한 폴백을 쓴다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import { parseThemeOffer, themeReturnUrls } from "./theme-store-api.ts";
import { readThemeReturnParams } from "./app-entry.ts";
import {
  canUseTheme,
  formatPriceKrw,
  indexOffers,
  markOwned,
  themeRow,
  type ThemeOffer,
} from "./theme-ownership.ts";

const FREE_THEME = { themeKey: "fresh_forest", premium: false };
const PAID_THEME = { themeKey: "aurora", premium: true };

function offers(...rows: ThemeOffer[]) {
  return indexOffers(rows);
}

function offer(over: Partial<ThemeOffer> = {}): ThemeOffer {
  return {
    themeKey: "aurora",
    free: false,
    owned: false,
    priceKrw: 4900,
    purchasable: true,
    ...over,
  };
}

describe("무료 테마", () => {
  it("FREE · [Use] · 언제나 쓸 수 있다", () => {
    const row = themeRow(FREE_THEME, offers(offer({ themeKey: "fresh_forest", free: true, owned: true, priceKrw: 0, purchasable: false })));
    assert.equal(row.state, "free");
    assert.equal(row.action, "use");
    assert.equal(row.usable, true);
  });

  it("카탈로그가 없어도 무료 테마는 쓸 수 있다", () => {
    // 카탈로그 장애가 무료 경험을 막으면 안 된다.
    const row = themeRow(FREE_THEME, indexOffers(null));
    assert.equal(row.state, "free");
    assert.equal(row.usable, true);
  });
});

describe("유료 · 미보유", () => {
  it("NOT OWNED · [Buy]", () => {
    const row = themeRow(PAID_THEME, offers(offer()));
    assert.equal(row.state, "not-owned");
    assert.equal(row.action, "buy");
    assert.equal(row.usable, false);
    assert.equal(row.priceKrw, 4900);
  });

  it("가격이 없으면 살 수 없다 — coming-soon", () => {
    // PM 이 가격을 정하지 않은 상태. [Buy] 를 보여 주면 눌러도 409 가 난다.
    const row = themeRow(PAID_THEME, offers(offer({ priceKrw: null, purchasable: false })));
    assert.equal(row.state, "coming-soon");
    assert.equal(row.action, "none");
    assert.equal(row.usable, false);
  });

  it("purchasable=false 면 가격이 있어도 사지 않는다", () => {
    const row = themeRow(PAID_THEME, offers(offer({ purchasable: false })));
    assert.equal(row.action, "none");
  });
});

describe("유료 · 보유", () => {
  it("OWNED · [Use]", () => {
    const row = themeRow(PAID_THEME, offers(offer({ owned: true, purchasable: false })));
    assert.equal(row.state, "owned");
    assert.equal(row.action, "use");
    assert.equal(row.usable, true);
  });

  it("보유하면 가격이 남아 있어도 [Buy] 가 아니다", () => {
    const row = themeRow(PAID_THEME, offers(offer({ owned: true, purchasable: true })));
    assert.equal(row.action, "use");
  });
});

describe("카탈로그 없음 폴백", () => {
  it("유료 테마를 사용 가능으로 보여 주지 않는다", () => {
    // **핵심 회귀**: 폴백이 관대하면 결제 없이 유료 테마가 열린다.
    const row = themeRow(PAID_THEME, indexOffers(null));
    assert.equal(row.state, "unknown");
    assert.equal(row.action, "none");
    assert.equal(row.usable, false);
  });

  it("서버가 themes.ts 와 다르게 말하면 서버를 따른다", () => {
    // THEME_PAID_KEYS 로 무료↔유료가 뒤집힐 수 있다. 프론트가 다시 판정하면
    // 눌러도 거절당하는 버튼이 생긴다.
    const nowFree = themeRow(PAID_THEME, offers(offer({ free: true, owned: true })));
    assert.equal(nowFree.state, "free");

    const nowPaid = themeRow(
      FREE_THEME,
      offers(offer({ themeKey: "fresh_forest", free: false, owned: false }))
    );
    assert.equal(nowPaid.state, "not-owned");
    assert.equal(nowPaid.usable, false);
  });
});

describe("선택 게이트", () => {
  it("보유·무료만 선택할 수 있다", () => {
    assert.equal(canUseTheme(PAID_THEME, offers(offer({ owned: true }))), true);
    assert.equal(canUseTheme(PAID_THEME, offers(offer({ owned: false }))), false);
    assert.equal(canUseTheme(FREE_THEME, offers(offer({ themeKey: "fresh_forest", free: true }))), true);
  });
});

describe("구매 직후 낙관적 갱신", () => {
  it("바로 OWNED 로 바뀐다", () => {
    // 결제 직후 NOT OWNED 로 남아 있으면 "돈은 나갔는데 안 샀다"로 보인다.
    const before = offers(offer());
    const after = markOwned(before, "aurora");
    assert.equal(themeRow(PAID_THEME, after).state, "owned");
    // 원본은 그대로 — 불변 갱신이라 서버 응답이 이겨도 꼬이지 않는다.
    assert.equal(themeRow(PAID_THEME, before).state, "not-owned");
  });

  it("모르는 key 는 아무 일도 하지 않는다", () => {
    const before = offers(offer());
    assert.deepEqual([...markOwned(before, "nope").keys()], ["aurora"]);
  });
});

describe("가격 표시", () => {
  it("원화로 천 단위 구분", () => {
    assert.equal(formatPriceKrw(4900), "₩4,900");
    assert.equal(formatPriceKrw(12000), "₩12,000");
  });

  it("가격이 없으면 null — 화면이 '준비 중'을 그린다", () => {
    assert.equal(formatPriceKrw(null), null);
    assert.equal(formatPriceKrw(0), null);
  });
});

describe("응답 파싱", () => {
  it("snake_case → camelCase", () => {
    const o = parseThemeOffer({
      theme_key: "aurora", free: false, owned: true, price_krw: 4900, purchasable: false,
    });
    assert.deepEqual(o, {
      themeKey: "aurora", free: false, owned: true, priceKrw: 4900, purchasable: false,
    });
  });

  it("price_krw 가 null 이면 null 이다 (0 으로 떨어뜨리지 않는다)", () => {
    // 0 으로 만들면 "무료"로 보인다 — 가격 미설정이 무료 배포가 된다.
    assert.equal(parseThemeOffer({ theme_key: "aurora", price_krw: null }).priceKrw, null);
  });

  it("빈 key 는 색인에서 걸러진다 — 찾을 수 없는 항목을 들고 다니지 않는다", () => {
    assert.equal(indexOffers([parseThemeOffer({ theme_key: "" })]).size, 0);
    assert.equal(indexOffers([{ ...offer(), themeKey: "" }]).size, 0);
    assert.equal(indexOffers([offer()]).size, 1);
  });
});

describe("테마 결제 복귀 파라미터", () => {
  it("성공 리다이렉트를 읽는다", () => {
    const p = readThemeReturnParams("?paymentKey=pk_1&orderId=eb_theme_x&amount=4900");
    assert.equal(p.paymentKey, "pk_1");
    assert.equal(p.orderId, "eb_theme_x");
    assert.equal(p.amount, 4900);
  });

  it("실패 리다이렉트의 code/message 를 읽는다", () => {
    const p = readThemeReturnParams("?code=PAY_PROCESS_CANCELED&message=%EC%B7%A8%EC%86%8C");
    assert.equal(p.code, "PAY_PROCESS_CANCELED");
    assert.equal(p.message, "취소");
    assert.equal(p.paymentKey, null);
  });

  it("amount 가 없거나 숫자가 아니면 null 이다", () => {
    // 대조값이 없어도 승인은 된다 — 기준은 서버가 보관한 주문 금액이다.
    assert.equal(readThemeReturnParams("?paymentKey=p&orderId=o").amount, null);
    assert.equal(readThemeReturnParams("?amount=abc").amount, null);
  });

  it("빈 쿼리에서도 죽지 않는다", () => {
    const p = readThemeReturnParams("");
    assert.equal(p.paymentKey, null);
    assert.equal(p.orderId, null);
  });
});

describe("결제 복귀 URL", () => {
  it("구독 복귀 경로와 겹치지 않는다", () => {
    // 겹치면 테마 결제가 구독 confirm 을 타게 되고, "테마 구매는 구독을
    // 건드리지 않는다"는 계약이 깨진다.
    const { successUrl, failUrl } = themeReturnUrls("https://eternalbeam.com");
    assert.equal(successUrl, "https://eternalbeam.com/themes/success");
    assert.equal(failUrl, "https://eternalbeam.com/themes/fail");
    assert.ok(!successUrl.includes("/billing/"));
  });

  it("origin 의 후행 슬래시를 중복시키지 않는다", () => {
    assert.equal(
      themeReturnUrls("https://eternalbeam.com/").successUrl,
      "https://eternalbeam.com/themes/success"
    );
  });
});
