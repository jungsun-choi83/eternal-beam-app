import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");
const i18n = readFileSync("src/components/memorial/memorial-i18n.ts", "utf8");

describe("홈 기본 동작 버튼", () => {
  it("처리된 펫 이미지가 있을 때만 버튼을 보여 준다", () => {
    const bottomAction = home.slice(home.indexOf("{/* Bottom Action */}"));
    assert.match(bottomAction, /\{cutoutImage \? \([\s\S]*onClick=\{onSaveToNFC\}[\s\S]*\) : null\}/);
  });

  it("기존 onSaveToNFC 계약을 유지한다", () => {
    assert.match(home, /onSaveToNFC: \(\) => void/);
    assert.match(home, /onClick=\{onSaveToNFC\}/);
  });

  it("실제 다음 단계인 미리보기를 안내한다", () => {
    assert.match(i18n, /saveToMemory: "미리보기로 계속"/);
    assert.match(i18n, /saveToMemory: "Continue to Preview"/);
  });
});
