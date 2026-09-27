import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const themes = readFileSync("src/components/memorial/themes.ts", "utf8");
const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");

describe("결제 왕복 배선 — EternalBeamApp", () => {
  it("결제 왕복 표식 처리는 순수 판단 함수를 거친다(레거시 persistThemeChoice 단독 호출로 되돌아가지 않는다)", () => {
    assert.match(
      app,
      /import \{ resolveThemePurchaseReturnApply \} from '@\/lib\/theme-purchase-return-apply'/,
    );
    const effectBlock = app.slice(
      app.indexOf("const { themeId, clearMarker } = resolveThemePurchaseReturnApply("),
      app.indexOf("const { themeId, clearMarker } = resolveThemePurchaseReturnApply(") + 700,
    );
    assert.match(effectBlock, /setSelectedTheme\(themeId\)/);
    assert.match(effectBlock, /persistThemeChoice\(themeId\)/);
  });

  it("표식 삭제는 clearMarker 로만 게이트된다 — 무조건 삭제로 되돌아가지 않는다", () => {
    const effectBlock = app.slice(
      app.indexOf("const { themeId, clearMarker } = resolveThemePurchaseReturnApply("),
      app.indexOf("}, [themePurchaseReturn])", app.indexOf("resolveThemePurchaseReturnApply(")),
    );
    assert.match(effectBlock, /if \(clearMarker\) clearThemePurchaseReturnState\(\)/);
    assert.doesNotMatch(effectBlock.replace(/if \(clearMarker\) clearThemePurchaseReturnState\(\)/, ""), /clearThemePurchaseReturnState\(\)/);
  });

  it("펫 자리 복원(petSlots useState)이 결제 왕복 effect 보다 먼저 선언된다 — 자리 복원이 항상 먼저 끝난다", () => {
    const petSlotsIndex = app.indexOf("const [petSlots, setPetSlots] = useState<PetIntakeSlot[]>");
    const effectIndex = app.indexOf("resolveThemePurchaseReturnApply(\n      themePurchaseReturn,");
    assert.ok(petSlotsIndex > -1 && effectIndex > -1);
    assert.ok(petSlotsIndex < effectIndex);
  });
});

describe("테마 카탈로그 — 소유권/가격 회귀 방지", () => {
  it("spring 테마는 되돌아오지 않는다(우발적 무료 테마 추가 금지)", () => {
    assert.doesNotMatch(themes, /themeKey:\s*"spring"/);
    assert.doesNotMatch(themes, /id:\s*12\b/);
  });

  it("premium 플래그는 pre-Phase-1 그대로다 — aurora/sunset/ocean_deep/custom_photo_bg 만 유료", () => {
    const premiumTrueCount = (themes.match(/premium:\s*true/g) ?? []).length;
    assert.equal(premiumTrueCount, 4);
  });

  it("테마 목록 개수가 늘어나지 않았다(11개 그대로)", () => {
    const ids = [...themes.matchAll(/^\s*id:\s*(?:[A-Z_]+|-?\d+),/gm)];
    assert.equal(ids.length, 11);
  });
});

describe("Phase 2 Home 대시보드 — 회귀 없음", () => {
  it("HomeScreen 계약이 이번 수정으로 바뀌지 않았다", () => {
    assert.match(home, /pets: HomePetSummary\[\]/);
    assert.match(home, /onSaveToNFC: \(\) => void/);
  });

  // Phase 11B-2 — 홈의 "마이 빔" 카드는 더 이상 로컬스토리지 불리언 플래그
  // (deviceConnected/isDevicePaired) 를 받지 않는다. 실제 게이트웨이 상태를
  // Device 화면과 같은 훅(useDeviceConnection)으로 직접 조회한다 — 진짜
  // 연결 상태·last-seen 만 보여 주고, 없으면 그냥 생략한다.
  it("HomeScreen 이 Device 화면과 같은 실시간 게이트웨이 훅을 쓴다(로컬 불리언 플래그로 되돌아가지 않는다)", () => {
    assert.doesNotMatch(home, /deviceConnected/);
    assert.match(home, /import \{ useDeviceConnection \} from ".\/use-device-connection"/);
  });

  it("EternalBeamApp 의 홈 배선(homePetSummaries/selectPetSlot)이 그대로다", () => {
    assert.match(app, /const homePetSummaries = petSlots\.map\(\(slot, index\) => \(\{/);
    assert.match(app, /onSelectPet=\{selectPetSlot\}/);
  });
});
