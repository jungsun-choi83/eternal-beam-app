import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

const home = readFileSync("src/components/memorial/home-screen.tsx", "utf8");
const app = readFileSync("src/app/EternalBeamApp.tsx", "utf8");
const desktopNav = readFileSync("src/components/layout/desktop-nav.tsx", "utf8");
const mediaTrigger = readFileSync("src/components/memorial/media-file-trigger.tsx", "utf8");
const homeCss = readFileSync("src/styles/home-dashboard.css", "utf8");

describe("홈 대시보드 — 나의 반려 / 라이브러리 / 마이 빔", () => {
  it("펫 목록을 실제 슬롯 배열에서 받아 그린다(가짜 목록을 만들지 않는다)", () => {
    assert.match(home, /pets: HomePetSummary\[\]/);
    assert.match(home, /pets\.map\(\(pet\)/);
    assert.match(home, /onClick=\{\(\) => onSelectPet\(pet\.index\)\}/);
  });

  it("자리가 남아 있을 때만 반려 추가 카드를 보여 준다(MAX_PET_SLOTS 재사용)", () => {
    assert.match(home, /import \{ MAX_PET_SLOTS \} from "@\/lib\/pet-slot-state"/);
    assert.match(home, /const canAddPet = pets\.length < MAX_PET_SLOTS/);
  });

  it("Gallery 목업이 더 이상 홈의 진입점이 아니다", () => {
    assert.doesNotMatch(home, /onGallery/);
    assert.doesNotMatch(home, /Grid3X3/);
  });

  it("My Library 는 실제 라이브러리 화면으로만 연결된다", () => {
    assert.match(home, /onLibrary\?:\s*\(\) => void/);
    assert.match(home, /onClick=\{onLibrary\}/);
  });

  it("My Beam 카드는 Device 화면과 같은 실시간 게이트웨이 상태만 쓰고 배터리·용량·펌웨어를 지어내지 않는다", () => {
    assert.match(home, /import \{ useDeviceConnection \} from "\.\/use-device-connection"/);
    assert.match(home, /resolvePairedDeviceId\(\)/);
    assert.doesNotMatch(home, /demoDeviceId\(\)/);
    assert.doesNotMatch(home, /battery/i);
    assert.doesNotMatch(home, /firmware/i);
    assert.doesNotMatch(home, /storage/i);
  });

  it("데스크톱 Home 은 중복 stage 프레임 없이 hero 를 메인 패널로 쓴다", () => {
    assert.match(app, /unframed=\{screen === 'home' \|\| AUTH_ENTRY_SCREENS\.has\(screen\)\}/);
  });

  it("avatar 메뉴가 계정 진입을 제공하므로 데스크톱 nav 에 Profile 을 중복하지 않는다", () => {
    assert.doesNotMatch(desktopNav, /label: "Profile"/);
  });

  it("파일 input 은 클릭 영역만 유지하고 native 파일명 UI 를 완전히 숨긴다", () => {
    assert.match(mediaTrigger, /opacity-0/);
    assert.doesNotMatch(mediaTrigger, /opacity-\[0\.02\]/);
  });

  it("Settings/Profile 진입(openSettings)을 그대로 보존한다", () => {
    assert.match(home, /onSettings\?:\s*\(\) => void/);
    assert.match(home, /onClick=\{onSettings\}/);
  });

  it("모바일은 콘텐츠 높이, 데스크톱은 대시보드가 정한 한 행 높이를 쓴다", () => {
    assert.match(
      homeCss,
      /\.eb-home__dashboard-grid\s*\{[^}]*--eb-home-card-row-height:\s*auto;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);[^}]*grid-auto-rows:\s*var\(--eb-home-card-row-height\);[^}]*align-items:\s*stretch;/s
    );
    assert.match(
      homeCss,
      /\.eb-home__dashboard-card\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*width:\s*100%;[^}]*max-width:\s*100%;[^}]*min-width:\s*0;[^}]*align-self:\s*stretch;/s
    );
    assert.doesNotMatch(
      homeCss,
      /\.eb-home__dashboard-card\s*\{[^}]*height:\s*100%/s
    );

    const desktopCss = homeCss.slice(homeCss.indexOf("@media (min-width: 1024px)"));
    assert.match(
      desktopCss,
      /\.eb-home__dashboard-grid\s*\{[^}]*--eb-home-card-row-height:\s*21rem;[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\);[^}]*grid-auto-rows:\s*var\(--eb-home-card-row-height\);/s
    );
    assert.match(
      homeCss,
      /\.eb-home__card-header\s*\{[^}]*min-height:\s*var\(--eb-home-card-header-height\);/s
    );
  });

  it("태블릿은 2 + 1로 감싸고 같은 행의 카드는 grid stretch로 맞춘다", () => {
    const tabletCss = homeCss.slice(
      homeCss.indexOf("@media (min-width: 640px)"),
      homeCss.indexOf("@media (min-width: 1024px)")
    );
    assert.match(tabletCss, /\.eb-home__dashboard-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
    assert.match(tabletCss, /\.eb-home__beam-card\s*\{[^}]*grid-column:\s*1\s*\/\s*-1;/s);
  });

  it("펫 카드는 390/768 에서 2열, 1024/1440 에서 3열로 배치한다", () => {
    const compactColumns = Number(
      homeCss.match(
        /\.eb-home__pets-track\s*\{[^}]*grid-template-columns:\s*repeat\((\d+),\s*minmax\(0,\s*1fr\)\);/s
      )?.[1]
    );
    const desktopBreakpoint = Number(
      homeCss.match(
        /@media \(min-width:\s*(\d+)px\)\s*\{(?:(?!@media)[\s\S])*?\.eb-home__pets-track/
      )?.[1]
    );
    const desktopCss = homeCss.slice(homeCss.indexOf(`@media (min-width: ${desktopBreakpoint}px)`));
    const desktopColumns = Number(
      desktopCss.match(
        /\.eb-home__pets-track\s*\{[^}]*grid-template-columns:\s*repeat\((\d+),\s*minmax\(0,\s*1fr\)\);/s
      )?.[1]
    );

    for (const [width, expected] of [[390, 2], [768, 2], [1024, 3], [1440, 3]] as const) {
      assert.equal(width >= desktopBreakpoint ? desktopColumns : compactColumns, expected);
    }

    assert.match(
      homeCss,
      /\.eb-home__pets-track\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%;[^}]*min-width:\s*0;/s
    );
    assert.match(
      homeCss,
      /\.eb-home__pet-card\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*100%;[^}]*min-width:\s*0;/s
    );
    assert.match(
      homeCss,
      /\.eb-home__pet-thumb\s*\{[^}]*width:\s*min\(3\.75rem,\s*100%\);[^}]*max-width:\s*100%;[^}]*height:\s*auto;[^}]*aspect-ratio:\s*1\s*\/\s*1;/s
    );
    assert.match(
      desktopCss,
      /\.eb-home__pet-card,\s*\.eb-home__pet-card--first\s*\{[^}]*min-height:\s*0;[^}]*\}[\s\S]*?\.eb-home__pet-card-body\s*\{[^}]*min-height:\s*0;/s
    );
  });

  it("빈 펫·Quick Start·Beam 미디어가 공통 카드의 남은 공간 안에서만 배치된다", () => {
    assert.match(homeCss, /\.eb-home__pets-track\s*\{[^}]*flex:\s*1\s+1\s+auto;[^}]*grid-auto-rows:\s*minmax\(0,\s*1fr\);/s);
    assert.match(homeCss, /\.eb-home__pet-card--first > div\s*\{[^}]*flex:\s*1\s+1\s+auto;/s);
    assert.match(homeCss, /\.eb-home__steps\s*\{[^}]*flex:\s*1\s+1\s+auto;[^}]*justify-content:\s*space-between;/s);
    assert.match(homeCss, /\.eb-home__beam-media\s*\{[^}]*aspect-ratio:\s*2\s*\/\s*1;[^}]*max-height:\s*13rem;/s);
    assert.match(homeCss, /\.eb-home__beam-media img\s*\{[^}]*object-fit:\s*cover;[^}]*object-position:\s*50%\s+60%;/s);

    const desktopCss = homeCss.slice(homeCss.indexOf("@media (min-width: 1024px)"));
    assert.match(
      desktopCss,
      /\.eb-home__beam-media\s*\{[^}]*flex:\s*1\s+1\s+auto;[^}]*min-height:\s*0;[^}]*max-height:\s*none;[^}]*aspect-ratio:\s*auto;/s
    );
  });
});

describe("EternalBeamApp — 홈 대시보드 배선", () => {
  it("펫 목록은 petSlots 에서 파생하고, 이름은 지어내지 않는다", () => {
    assert.match(app, /const homePetSummaries = petSlots\.map\(\(slot, index\) => \(\{/);
    assert.match(app, /pets=\{homePetSummaries\}/);
    assert.match(app, /activePetIndex=\{activePetSlotIndex\}/);
    assert.match(app, /onSelectPet=\{selectPetSlot\}/);
  });

  // Phase 11B-2 — HomeScreen 은 더 이상 isDevicePaired() 불리언 플래그를 받지
  // 않는다. Device 화면과 같은 useDeviceConnection 훅으로 직접 실시간 상태를
  // 조회한다(가짜 연결 상태 없음, 배선은 home-dashboard.test.ts 위 블록에서 확인).
  it("EternalBeamApp 은 여전히 schedulePiDiscovery 를 배선한다(Pi 데모 경로 보존)", () => {
    assert.match(app, /import \{ schedulePiDiscovery \} from '@\/lib\/pi-sensor-bridge'/);
  });

  it("펫 이름은 기존 pet-profile 저장값을 그대로 읽는다", () => {
    assert.match(app, /import \{ getPetName \} from '@\/lib\/pet-profile'/);
    assert.match(app, /petName=\{getPetName\(\) \|\| undefined\}/);
  });

  it("Home 은 데스크톱에서 넓은 대시보드 폭을 쓴다(AppShell wide 옵트인)", () => {
    // Phase 3 — Pet Upload/Intake 도 데스크톱에서 펫 레일 + 스테이지의 2단
    // 배치가 필요해 같은 옵트인에 합류했다(둘 다 여러 섹션을 나란히 두는
    // 화면). 폰 목업형 단일 흐름 화면(테마 선택 등)은 그대로 520px.
    //
    // Phase 6 — preview 는 이제 Composer(메인 작업 공간)다. 조정 단계부터
    // 항상 wide 스테이지를 쓴다 — 생성 진행 중일 때만 넓히던 Phase 4 의
    // 임시 옵트인은 더 이상 필요 없다.
    //
    // Phase 8 — My Beam(device)도 개요 + 상태 패널 + 최근 전송 내역을 나란히
    // 두는 대시보드형 화면이라 같은 옵트인에 합류했다.
    //
    // 런치 감사 정리 — My Library/Settings 는 이제까지 이 옵트인에서
    // 빠져 있어서 데스크톱에서도 520px 폰 목업 카드로 렌더됐다(재설계가
    // 아니라 이미 있던 wide 옵트인 목록에서 빠졌던 누락). 둘 다 그리드/폼을
    // 나란히 두는 화면이라 같은 옵트인에 합류한다.
    assert.match(
      app,
      /wide=\{screen === 'home' \|\| screen === 'photoUpload' \|\| screen === 'themeSelection' \|\| screen === 'preview' \|\| screen === 'device' \|\| screen === 'library' \|\| screen === 'settings'\}/
    );
  });
});
