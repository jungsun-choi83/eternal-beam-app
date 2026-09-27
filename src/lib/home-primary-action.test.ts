import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");
const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const i18n = readFileSync("src/components/memorial/memorial-i18n.ts", "utf8");

describe("홈 히어로 — 영구 브랜드 콘텐츠", () => {
  it("히어로 이미지는 승인된 브랜드 자산 하나다", () => {
    assert.match(home, /const HOME_HERO_IMAGE = "\/home\/hero-golden-retriever\.jpg"/);
    assert.match(home, /src=\{HOME_HERO_IMAGE\}/);
  });

  it("히어로는 펫 누끼·업로드 이미지를 절대 읽지 않는다(업로드·펫 전환·복원·라이브러리 복귀에 영향받지 않는다)", () => {
    assert.doesNotMatch(home, /cutoutImage/);
    assert.doesNotMatch(home, /previewImage\}[^]*?eb-home__hero-img/);
    // 펫 미리보기는 "나의 반려" 카드에서만 쓰인다.
    assert.match(home, /className="eb-home__pet-thumb">\s*<img src=\{pet\.previewImage\}/);
  });

  it("히어로 카피: Eternal Beam / Their love lives on. / 짧은 보조 문구", () => {
    assert.match(home, /\{texts\.heroEyebrow\}/);
    assert.match(home, /\{texts\.heroHeadline\}/);
    assert.match(home, /\{texts\.heroBody\}/);
    assert.match(i18n, /heroEyebrow: "Eternal Beam"/);
    assert.match(i18n, /heroHeadline: "Their love lives on\."/);
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

  it("보조 CTA 는 기존 Library 흐름으로만 간다", () => {
    assert.match(home, /onClick=\{onLibrary\}[^>]*>\s*\{texts\.heroCtaLibrary\}/);
  });

  it("App: Create a Memory 는 업로드·펫 추가 흐름으로 간다(미리보기가 아니다)", () => {
    assert.match(app, /const startCreateMemory = \(\) => \{/);
    assert.match(app, /onAddPet=\{startAddPet\}/);
    const nfc = app.slice(app.indexOf("onSaveToNFC={() => {"), app.indexOf("}}", app.indexOf("onSaveToNFC={() => {")));
    assert.match(nfc, /startCreateMemory\(\)/);
    assert.doesNotMatch(nfc, /navigateTo\('preview'\)/);
    const create = app.slice(app.indexOf("const startCreateMemory"), app.indexOf("const startAddPet"));
    assert.match(create, /navigateTo\('photoUpload'\)/);
    assert.doesNotMatch(create, /'preview'/);
  });
});

describe("홈 — 새 사용자 / 돌아온 사용자", () => {
  it("펫 카드는 실제 슬롯만 그린다(0/1/2/3 규칙은 home-pet-cards 에서 검증)", () => {
    assert.match(home, /import \{ planHomePetCards \} from "@\/lib\/home-pet-cards"/);
    assert.match(home, /planHomePetCards\(pets, MAX_PET_SLOTS\)/);
    assert.match(home, /petCards\.showAddFirstPet \?/);
    assert.match(home, /\{texts\.addFirstPet\}/);
    assert.match(home, /\{texts\.addFirstPetHint\}/);
    assert.match(home, /showAddPet \?/);
  });

  it("이름·품종·썸네일을 지어내지 않는다 — 라벨은 등록된 이름 아니면 자리 번호", () => {
    assert.match(home, /petName\.trim\(\) : texts\.petLabel\(index \+ 1\)/);
    assert.doesNotMatch(home, /breed/i);
  });
});

describe("홈 — My Beam 카드", () => {
  it("연결됨 / 오프라인 / 설정 전 — 살아 있는 게이트웨이 상태만 쓴다", () => {
    assert.match(home, /import \{ homeBeamCardState[^}]*\} from "@\/lib\/home-beam-state"/);
    assert.match(home, /texts\.beamConnected/);
    assert.match(home, /texts\.beamOffline/);
    assert.match(home, /texts\.beamNotSetUp/);
    assert.match(i18n, /beamNotSetUp: "Not set up"/);
  });

  it("기기 id 는 내부용 — 화면에 그리지 않는다", () => {
    assert.doesNotMatch(home, /\{deviceId\}/);
    assert.doesNotMatch(home, /beam-001/);
    assert.doesNotMatch(home, /lastSeen/);
  });
});

describe("언어 전환 배치", () => {
  it("홈에는 떠 있는 KR/EN 컨트롤이 없다 — 데스크톱은 사용자 메뉴, 모바일은 설정", () => {
    assert.doesNotMatch(home, /LanguageToggle/);
    const userMenu = readFileSync("src/components/layout/user-menu.tsx", "utf8");
    assert.match(userMenu, /<LanguageToggle language=\{language\} onChange=\{onChangeLanguage\} \/>/);
    const settings = readFileSync("src/components/memorial/settings-screen.tsx", "utf8");
    assert.match(settings, /case "language":\s*onChangeLanguage\(\)/);
    // 앱 셸에는 MobileFrame 시절의 떠 있는 토글이 남아 있지 않다.
    assert.doesNotMatch(app, /<LanguageToggle/);
  });
});
