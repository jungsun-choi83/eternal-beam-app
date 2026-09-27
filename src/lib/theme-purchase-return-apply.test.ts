import { strict as assert } from "node:assert";
import { describe, it } from "node:test";
import { resolveThemePurchaseReturnApply } from "./theme-purchase-return-apply.ts";

const lookup = (map: Record<string, number>) => (themeKey: string) => map[themeKey];

describe("결제 왕복 표식 적용 판단", () => {
  it("표식이 없으면 아무것도 하지 않는다", () => {
    const result = resolveThemePurchaseReturnApply(null, lookup({}));
    assert.deepEqual(result, { themeId: null, clearMarker: false });
  });

  it("결제를 확정하지 못하고 돌아왔으면 테마는 건드리지 않고 표식만 지운다", () => {
    const result = resolveThemePurchaseReturnApply(
      { themeKey: "aurora", confirmed: false },
      lookup({ aurora: 5 }),
    );
    assert.deepEqual(result, { themeId: null, clearMarker: true });
  });

  it("펫 자리에 이미 스냅샷이 있던 경우에도(=조회기가 다른 값을 알든 말든) 확정된 결제 테마를 그대로 authoritative 하게 돌려준다", () => {
    // "슬롯 스냅샷 존재" 시나리오의 본질은 activePetSlotIndex 가 이미 복원돼
    // 있다는 것 — 이 함수는 그 자리 번호를 몰라도 되고(호출부가 그 자리에
    // setSelectedTheme 을 적용한다), 오직 "이 themeKey 로 무엇을 해야 하는가"만
    // 답한다.
    const result = resolveThemePurchaseReturnApply(
      { themeKey: "aurora", confirmed: true },
      lookup({ aurora: 5 }),
    );
    assert.deepEqual(result, { themeId: 5, clearMarker: true });
  });

  it("스냅샷이 전혀 없던 새 세션(fresh pet)에서도 같은 결과를 준다", () => {
    // fresh 경로는 petSlots 초기화에서 이미 themeKey 를 한 번 반영하지만, 이
    // 함수는 그 사실을 모른 채로도 같은 themeId 를 돌려줘야 한다 — 두 번째
    // 반영은 멱등이다(같은 값을 다시 쓸 뿐).
    const result = resolveThemePurchaseReturnApply(
      { themeKey: "sunset", confirmed: true },
      lookup({ sunset: 6 }),
    );
    assert.deepEqual(result, { themeId: 6, clearMarker: true });
  });

  it("확정된 결제인데 themeKey 를 모르면(카탈로그 변경·손상) 표식을 지우지 않는다 — 확인된 결제를 조용히 잃지 않는다", () => {
    const result = resolveThemePurchaseReturnApply(
      { themeKey: "unknown_removed_theme", confirmed: true },
      lookup({ aurora: 5 }),
    );
    assert.deepEqual(result, { themeId: null, clearMarker: false });
  });

  it("marker clearing order: 테마 적용과 표식 삭제는 항상 함께(같은 호출)에서 결정된다 — 부분 적용 상태가 없다", () => {
    const confirmedKnown = resolveThemePurchaseReturnApply(
      { themeKey: "ocean_deep", confirmed: true },
      lookup({ ocean_deep: 7 }),
    );
    assert.equal(confirmedKnown.themeId !== null, confirmedKnown.clearMarker);

    const confirmedUnknown = resolveThemePurchaseReturnApply(
      { themeKey: "ocean_deep", confirmed: true },
      lookup({}),
    );
    assert.equal(confirmedUnknown.themeId, null);
    assert.equal(confirmedUnknown.clearMarker, false);
  });
});
