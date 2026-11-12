import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");
const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const i18n = readFileSync("src/components/memorial/memorial-i18n.ts", "utf8");

describe("홈 히어로 — 영구 브랜드 콘텐츠", () => {
  it("히어로 이미지는 승인된 브랜드 자산 하나다", () => {
    assert.match(home, /const HOME_HERO_IMAGE = "\/auth\/entry-meadow\.jpg"/);
    assert.match(home, /src=\{HOME_HERO_IMAGE\}/);
  });

  it("히어로는 펫 누끼·업로드 이미지를 절대 읽지 않는다(업로드·펫 전환·복원·라이브러리 복귀에 영향받지 않는다)", () => {
    assert.doesNotMatch(home, /cutoutImage/);
    assert.doesNotMatch(home, /pet\.previewImage/);
    assert.doesNotMatch(home, /eb-home__pet-thumb/);
  });

  it("히어로 카피: Eternal Beam / 풍경 속 문장 / 짧은 보조 문구", () => {
    assert.match(home, /\{texts\.heroEyebrow\}/);
    assert.match(home, /\{texts\.heroHeadline\}/);
    assert.match(home, /\{texts\.heroBody\}/);
    assert.match(i18n, /heroEyebrow: "Eternal Beam"/);
    assert.match(i18n, /heroHeadline: "Keep your companion\\nin the landscape they loved\."/);
  });
});

describe("홈 히어로 CTA", () => {
  it("기존 onSaveToNFC 계약을 유지한다", () => {
    assert.match(home, /onSaveToNFC: \(\) => void/);
    assert.match(home, /onClick=\{onSaveToNFC\}/);
  });

  it("주 CTA 는 항상 'Create a Memory' — 상태에 따라 미리보기 문구로 바뀌지 않는다", () => {
    assert.match(home, /\{texts\.heroCtaCreate\}/);
    assert.doesNotMatch(home, /texts\.saveToMemory/);
    assert.doesNotMatch(home, /texts\.addMedia/);
  });

  it("홈에는 내 장면 블록이 없다", () => {
    assert.doesNotMatch(home, /texts\.scenesTitle/);
    assert.doesNotMatch(home, /eb-home__quiet/);
    assert.doesNotMatch(home, /onClick=\{onLibrary\}/);
  });

  it("App: 시작하기는 회원가입, 이어서 기기 연결로 간다", () => {
    assert.match(app, /const beginOnboarding = async \(\) => \{/);
    const nfc = app.slice(app.indexOf("onSaveToNFC={() => {"), app.indexOf("}}", app.indexOf("onSaveToNFC={() => {")));
    assert.match(nfc, /beginOnboarding\(\)/);
    assert.doesNotMatch(nfc, /navigateTo\('preview'\)/);
    const begin = app.slice(app.indexOf("const beginOnboarding"), app.indexOf("const startCreateMemory"));
    assert.match(begin, /navigateTo\(\(await hasSession\(\)\) \? 'deviceConnect' : 'signup'\)/);
    assert.match(app, /completedMode === 'login' \? 'home' : 'deviceConnect'/);
  });
});

describe("홈 — 환영 화면만", () => {
  it("장면 목록과 기기 상태를 홈에 그리지 않는다", () => {
    assert.doesNotMatch(home, /planHomePetCards/);
    assert.doesNotMatch(home, /scenesEmptyTitle/);
    assert.doesNotMatch(home, /useDeviceConnection/);
    assert.doesNotMatch(home, /beamConnected/);
    assert.doesNotMatch(home, /beamNeedsRegister/);
    assert.doesNotMatch(home, /\{deviceId\}/);
    assert.doesNotMatch(home, /breed/i);
    assert.doesNotMatch(home, /battery/i);
  });
});

describe("언어 전환 배치", () => {
  it("홈과 상단 내비게이션에 KO | EN 토글이 있다", () => {
    assert.match(home, /<LanguageToggle language=\{language\} onChange=\{onLanguageChange\}/);
    assert.match(app, /<LanguageToggle language=\{language\} onChange=\{handleLanguageChange\} \/>/);
    const userMenu = readFileSync("src/components/layout/user-menu.tsx", "utf8");
    assert.match(userMenu, /<LanguageToggle language=\{language\} onChange=\{onChangeLanguage\} \/>/);
    const settings = readFileSync("src/components/memorial/settings-screen.tsx", "utf8");
    assert.match(settings, /case "language":\s*onChangeLanguage\(\)/);
  });
});
