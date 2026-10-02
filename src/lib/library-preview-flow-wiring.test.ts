/**
 * My Library → Theme → Preview — 라이브러리 모드 배선 (프론트엔드 전용 수정).
 *
 * ── 고친 결함 ────────────────────────────────────────────────────────────
 *   1. 라이브러리 경로가 잠깐 예전 "누끼 필요" 경고를 보여줬다 — 라이브러리
 *      모드 여부가 sessionStorage 를 읽는 effect 가 돈 **뒤에야** 정해졌다
 *      (첫 페인트는 항상 업로드 취급이었다).
 *   2. 업로드 슬롯 상태와 라이브러리 발행 상태가 같은 sessionStorage 키를
 *      공유해 섞였다.
 *   3. 테마 미리보기가 packed_alpha 를 명시로 받지 않았다(휴리스틱 감지에
 *      기댔다).
 *   4. my-library-screen 이 background_baked 를 항상 false 로 지어냈다.
 *
 * 고침: MyLibraryScreen 이 isLibraryFlow + libraryPublication 을 **동기적으로**
 * props 로 내려준다(sessionStorage 왕복 없음). 테마 선택·미리보기 두 화면
 * 모두 pipeline 을 applyLibraryOverride 로 덮어쓴 **뒤에만** 읽는다 — 그래서
 * sessionStorage 쓰기가 실패하거나 이전 업로드 세션의 값이 남아 있어도
 * 라이브러리 발행 메타데이터가 이긴다.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { applyLibraryOverride, type LibraryPublication } from "./library-publication.ts";
import { fetchMyLibraryPets } from "./my-library-api.ts";
import type { StoredPipeline } from "../components/memorial/ai-processing-screen.ts";

const read = (p: string) => readFileSync(p, "utf8");

const THEME_SEL = "src/components/memorial/theme-selection-screen.tsx";
const PREVIEW = "src/components/memorial/preview-screen.tsx";
const MY_LIBRARY = "src/components/memorial/my-library-screen.tsx";
const MY_LIBRARY_API = "src/lib/my-library-api.ts";

const PUB: LibraryPublication = {
  petId: "pet_123",
  idleVideoUrl: "https://signed.test/breathing.mp4",
  deliveryFormat: "packed_alpha",
  backgroundBaked: true,
};

// ── applyLibraryOverride: 순수 판정 — 정본이 항상 이긴다 ───────────────────

test("applyLibraryOverride: 라이브러리 모드가 아니면 손대지 않는다 — 업로드 흐름 불변", () => {
  const upload: StoredPipeline = {
    content_id: "upload-1",
    cutout_display_url: "https://cutout.test/a.png",
    dog_only_nobg_url: "",
    idle_video_url: "",
    action_video_url: "",
  };
  assert.equal(applyLibraryOverride(upload, false, PUB), upload);
});

test("applyLibraryOverride: sessionStorage 가 비어 있어도(쿼터 실패) 발행 메타데이터로 채운다", () => {
  const out = applyLibraryOverride(null, true, PUB);
  assert.ok(out);
  assert.equal(out!.idle_video_url, PUB.idleVideoUrl);
  assert.equal(out!.delivery_format, "packed_alpha");
  assert.equal(out!.background_baked, true);
  assert.equal(out!.generation_source, "library");
  assert.equal(out!.content_id, PUB.petId);
});

test("applyLibraryOverride: 낡은 업로드 슬롯 값(pet_id/영상/포맷/baked)이 남아 있어도 발행 메타데이터가 이긴다", () => {
  const stale: StoredPipeline = {
    content_id: "previous-upload-session",
    cutout_display_url: "",
    dog_only_nobg_url: "",
    idle_video_url: "https://stale.test/old-upload.mp4",
    action_video_url: "",
    delivery_format: "blackkey",
    background_baked: false,
    generation_source: "phase7-run",
  };
  const out = applyLibraryOverride(stale, true, PUB)!;
  assert.equal(out.idle_video_url, PUB.idleVideoUrl, "낡은 업로드 영상 URL 이 남아 있다");
  assert.equal(out.delivery_format, "packed_alpha", "낡은 delivery_format 이 남아 있다");
  assert.equal(out.background_baked, true, "낡은 background_baked 가 남아 있다");
  assert.equal(out.generation_source, "library", "generation_source 가 라이브러리로 안 바뀌었다");
});

test("applyLibraryOverride: 발행 메타데이터가 없으면(펫/모션 미선택) 손대지 않는다", () => {
  const stored: StoredPipeline = {
    content_id: "x",
    cutout_display_url: "",
    dog_only_nobg_url: "",
    idle_video_url: "",
    action_video_url: "",
  };
  assert.equal(applyLibraryOverride(stored, true, null), stored);
});

// ── 테마 선택 화면: 경고 배너가 prop 으로, 첫 렌더부터 결정된다 ────────────

test("테마 선택: isLibraryFlow prop 을 선언한다 — sessionStorage effect 를 기다리지 않는다", () => {
  const src = read(THEME_SEL);
  assert.match(src, /isLibraryFlow\?: boolean;/, "명시 prop 이 없다");
  assert.match(src, /libraryPublication\?: LibraryPublication \| null;/, "발행 메타데이터 prop 이 없다");
  // 초기 state 가 prop 에서 바로 시작한다 — useEffect 의 setState 를 기다리면
  // 첫 페인트가 항상 업로드 흐름(경고 노출)으로 그려진다.
  assert.match(
    src,
    /useState\(isLibraryFlow\)/,
    "isLibrarySource 초기값이 prop 이 아니다 — 첫 페인트가 여전히 업로드로 그려진다"
  );
});

test("테마 선택: 라이브러리 흐름에서는 절대 뜨지 않고, 업로드 흐름에서는 그대로 뜬다", () => {
  const src = read(THEME_SEL);
  // 배너 게이트 — isLibrarySource 가 prop 에서 확정되므로 라이브러리에서는
  // 항상 거짓이 되어 배너가 렌더되지 않는다.
  assert.match(
    src,
    /\{cutoutReadiness === "missing" && !isLibrarySource \? \(/,
    "durable readiness 배너 게이트가 사라지거나 바뀌었다",
  );
});

test("테마 선택: sessionStorage 에서 읽은 값을 발행 메타데이터로 덮어쓴 뒤에만 쓴다", () => {
  const src = read(THEME_SEL);
  assert.match(
    src,
    /applyLibraryOverride\(stored, isLibraryFlow, libraryPublication\)/,
    "override 를 거치지 않고 pipeline 을 바로 쓴다 — 낡은 값이 새어 들어갈 수 있다"
  );
});

// ── 테마 선택 화면: Phase 11B-4 이후로는 펫/배경 합성을 전혀 하지 않는다 ────
//
// 카드·큰 미리보기·PetIdleDisplay·CutoutStage 는 그리드 재설계로 화면 밖으로
// 나갔다(펫+테마 합성은 PreviewScreen 하나가 맡는다) — 그 사실 자체는
// theme-selection-redesign.test.ts 가 지킨다. 여기서는 라이브러리 배선(prop
// 계약·경고 배너 게이트·pipeline override)만 계속 지킨다.

// ── 미리보기 화면: 같은 계약, 같은 전제 ────────────────────────────────────

test("미리보기: isLibraryFlow/libraryPublication prop 이 있고 pipeline 을 지연 초기화한다", () => {
  const src = read(PREVIEW);
  assert.match(src, /isLibraryFlow\?: boolean;/);
  assert.match(src, /libraryPublication\?: LibraryPublication \| null;/);
  assert.match(
    src,
    /useState<StoredPipeline \| null>\(\(\) =>\s*\n\s*applyLibraryOverride\(readStoredPipeline\(\), isLibraryFlow, libraryPublication\)/,
    "pipeline 이 effect 가 돌 때까지 null 로 남아 있다 — 첫 페인트가 업로드로(빈 화면) 그려진다"
  );
});

test("미리보기: pet_id 도 라이브러리 발행 메타데이터가 이긴다 (PremiumAssetsProvider)", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /props\.isLibraryFlow && props\.libraryPublication\s*\n\s*\? props\.libraryPublication\.petId\s*\n\s*: readPipelinePetId\(\)/,
    "petId 가 여전히 sessionStorage 파생값(getEternalBeamPetId)만 본다"
  );
});

test("미리보기: 원본 사진 갈래는 라이브러리 경로에서도 펫을 두 번 그리지 않는다", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /\(cutoutDisplay \|\| \(hasIdle && isLibrarySource\)\) && !isOriginalPhotoTheme/,
    "라이브러리 분기가 !isOriginalPhotoTheme 가드 밖으로 나갔다"
  );
});

// ── My Library 화면: background_baked 하드코딩 제거 + props 배선 ──────────

test("My Library: background_baked 를 더 이상 지어내지 않는다 — 서버 값을 그대로 쓴다", () => {
  const src = read(MY_LIBRARY);
  assert.ok(
    !/background_baked:\s*false,/.test(src),
    "여전히 하드코딩된 background_baked: false 가 있다"
  );
  assert.match(src, /background_baked:\s*motion\.backgroundBaked,/);
});

test("My Library: 테마 선택·미리보기 두 화면 모두 isLibraryFlow 와 발행 메타데이터를 동기적으로 받는다", () => {
  const src = read(MY_LIBRARY);

  const themeCallIdx = src.indexOf("<ThemeSelectionScreen");
  assert.ok(themeCallIdx > 0, "ThemeSelectionScreen 호출부가 없다");
  const themeCall = src.slice(themeCallIdx, src.indexOf("onBack", themeCallIdx));
  assert.match(themeCall, /\bisLibraryFlow\b/, "테마 선택에 prop 이 없다");
  assert.match(themeCall, /libraryPublication=\{libraryPublication\}/, "발행 메타데이터가 흐르지 않는다");

  const previewCallIdx = src.indexOf("<PreviewScreen");
  assert.ok(previewCallIdx > 0, "PreviewScreen 호출부가 없다");
  const previewCall = src.slice(previewCallIdx, src.indexOf("onBack", previewCallIdx));
  assert.match(previewCall, /\bisLibraryFlow\b/, "미리보기에 prop 이 없다");
  assert.match(previewCall, /libraryPublication=\{libraryPublication\}/, "발행 메타데이터가 흐르지 않는다");
});

test("My Library: cutoutImage 를 요구하지 않는다 — 두 화면 호출부 모두 null 을 넘긴다", () => {
  const src = read(MY_LIBRARY);
  const matches = [...src.matchAll(/cutoutImage=\{null\}/g)];
  assert.equal(matches.length, 2, "두 화면 호출부 모두 cutoutImage={null} 이어야 한다");
});

// ── my-library-api: 서버가 이미 돌려주는 background_baked 를 버리지 않는다 ──

test("my-library-api: PublishedLibraryMotion 이 backgroundBaked 를 실어 나른다", () => {
  const src = read(MY_LIBRARY_API);
  assert.match(src, /backgroundBaked:\s*boolean;/, "타입에 필드가 없다");
  assert.match(
    src,
    /backgroundBaked:\s*motion\.background_baked === true,/,
    "서버 응답을 파싱하지 않는다 — 백엔드는 이미 이 필드를 돌려준다(PublishedBreathingOut)"
  );
});

// ── 기기 송출: 새 sendDeviceCommand() 하나만 — 레거시 Pi/S23 경로 금지 ──────
//
// 하드웨어 개발자의 Pi/APK 는 새 backend device gateway + sendDeviceCommand()
// 를 통해서만 연결된 외부 클라이언트로 취급한다. 라이브러리 경로가 별도
// 기기-동기화 구현을 새로 만들면 안 된다 — 새로 생성된 펫과 같은 경로여야
// 두 경로가 갈라져 한쪽만 깨지는 일이 없다.

const LEGACY_DEVICE_SYMBOLS = [
  "schedulePetReadyToDevice",
  "sendPhase7MotionToDevice",
  "scheduleThemeBackgroundSync",
  "triggerThemeOnDevice",
  "device-renderer",
  "/demo/play",
  "/demo/pet-ready",
  ":9999",
  ":5005",
  "pi-sensor-bridge",
];

test("My Library: 기기 송출은 sendDeviceCommand() 하나 — theme_play 뒤 pet_asset", () => {
  const src = read(MY_LIBRARY);
  assert.match(
    src,
    /import \{ resolvePairedDeviceId, sendDeviceCommand \} from "@\/lib\/device-command-api";/,
  );
  const themeIdx = src.indexOf('event: "theme_play"');
  const petIdx = src.indexOf('event: "pet_asset"');
  assert.ok(themeIdx > 0 && petIdx > 0 && themeIdx < petIdx, "theme_play 가 pet_asset 보다 먼저여야 한다");
  // 두 이벤트 모두 sendDeviceCommand 호출 안에 있다 — 다른 헬퍼로 보내지 않는다.
  const themeCallStart = src.lastIndexOf("sendDeviceCommand(", themeIdx);
  const petCallStart = src.lastIndexOf("sendDeviceCommand(", petIdx);
  assert.ok(themeCallStart > 0 && petCallStart > 0);
});

test("My Library: beam-001 은 무조건 쓰지 않는다 — 페어링된 device_id 가 없으면 명령을 하나도 보내지 않는다 (P0 #3)", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const onPlayOnBeamStart = src.indexOf("onPlayOnBeam={async () => {", stepBlockStart);
  const onChangeMotionStart = src.indexOf("onChangeMotion={", onPlayOnBeamStart);
  const callback = src.slice(onPlayOnBeamStart, onChangeMotionStart);

  const guardIdx = callback.indexOf("resolvePairedDeviceId()");
  const noDeviceReturnIdx = callback.indexOf('reason: "no_paired_device"');
  const themeCallIdx = callback.indexOf("sendDeviceCommand({");
  assert.ok(guardIdx > 0, "resolvePairedDeviceId() 를 부르지 않는다 — 여전히 하드코딩된 device_id 를 쓴다");
  assert.ok(
    noDeviceReturnIdx > 0 && noDeviceReturnIdx < themeCallIdx,
    "device_id 가 없을 때 진짜 실패를 돌려주는 가드가 sendDeviceCommand 보다 먼저 있어야 한다",
  );
  assert.equal(callback.includes("demoDeviceId"), false, "beam-001 데모 상수를 여전히 무조건 쓴다");
});

test("My Library: 레거시 Pi/S23/UDP/데모 기기 경로를 참조하지 않는다", () => {
  const src = read(MY_LIBRARY);
  for (const symbol of LEGACY_DEVICE_SYMBOLS) {
    assert.ok(!src.includes(symbol), `레거시 심볼이 남아 있다: ${symbol}`);
  }
});

test("테마 선택/미리보기 화면도 라이브러리 전용 기기-동기화를 새로 만들지 않았다", () => {
  for (const path of [THEME_SEL, PREVIEW]) {
    const src = read(path);
    for (const symbol of LEGACY_DEVICE_SYMBOLS) {
      if (symbol === "schedulePetReadyToDevice" && path === PREVIEW) {
        // PreviewScreen 의 레거시(사전-Phase7) 생성 경로에 이미 있던 호출이다.
        // hasIdle 이 true 인 라이브러리 흐름은 handleConfirm 이 그 경로에
        // 도달하기 전에 즉시 onComplete() 로 반환하므로 실행되지 않는다 —
        // 여기서 요구하는 것은 "새로 추가하지 않는다"이지 "전혀 없다"가 아니다.
        continue;
      }
      assert.ok(!src.includes(symbol), `${path}: 레거시 심볼이 남아 있다: ${symbol}`);
    }
  }
});

test("미리보기: 라이브러리 흐름은 새 기기 명령 전에 handleConfirm 이 즉시 반환한다 (레거시 경로 도달 불가)", () => {
  const src = read(PREVIEW);
  assert.match(
    src,
    /if \(hasIdle\) \{\s*\n\s*onComplete\(\);\s*\n\s*return;\s*\n\s*\}/,
    "hasIdle 게이트가 사라졌다 — 라이브러리 흐름이 레거시 생성/기기 경로를 탈 수 있다"
  );
});

// ── 미리보기: 낡은 SHOW_PIPELINE_DEBUG(Luma→Unity) 패널 제거 ────────────────
//
// 이 패널은 실제 웹 컴포지터(theme-preview-frame) **아래에** 별도로 그려지던
// 죽은 QA 스크래치판이었다 — "Pipeline (Luma → Unity background)" 문구를
// 달고, packed_alpha 소스를 transparentComposite={false} 로 강제해 RGB+알파
// 매트를 합성 없이 그대로(raw) 보여줬다. isLibraryFlow 와 무관하게 dev 모드면
// 항상 나타나 실제로는 정상인 상단 컴포지터를 깨진 것처럼 보이게 만들었다.
// 대체 프리뷰 없이 완전히 제거한다 — 위 theme-preview-frame 컴포지터가 이미
// 유일한 미리보기다.

test("미리보기: SHOW_PIPELINE_DEBUG 패널과 그 문구를 더 이상 갖고 있지 않다", () => {
  const src = read(PREVIEW);
  for (const symbol of [
    "SHOW_PIPELINE_DEBUG",
    "pipelineTitle",
    "pipelineHint",
    "unityPlaceholder",
    "noLuma",
    "tryFfmpegPreview",
    "ffmpegTry",
    "ffmpegLoading",
    "ffPreviewUrl",
    "ffLoading",
    "ffError",
    "generatePreview",
    "getVideoApiBaseUrl",
    "getThemeBackgroundApiId",
  ]) {
    assert.ok(!src.includes(symbol), `레거시 디버그 패널 잔재가 남아 있다: ${symbol}`);
  }
});

test("미리보기: IdleLoopVideo 를 더 이상 직접 import/사용하지 않는다 — 유일한 재생 경로는 PetIdleDisplay", () => {
  const src = read(PREVIEW);
  assert.ok(
    !src.includes("IdleLoopVideo"),
    "IdleLoopVideo 를 직접 부르는 곳이 남아 있다 — packed_alpha 를 raw 로 새는 경로가 될 수 있다"
  );
  // PetIdleDisplay(내부에서 IdleLoopVideo 를 packed_alpha 인지 알고 부른다)만 남는다.
  assert.match(src, /<PetIdleDisplay/);
});

test("미리보기: 패널을 대체할 새 미리보기를 만들지 않았다 — theme-preview-frame 하나만 남는다", () => {
  const src = read(PREVIEW);
  const matches = [...src.matchAll(/theme-preview-frame/g)];
  assert.ok(matches.length > 0, "기존 컴포지터 프레임 클래스가 사라졌다");
  // motion.div 미리보기 컨테이너가 정확히 하나다 — 디버그 패널이 쓰던
  // "mt-5 w-full max-w-[340px]" 같은 두 번째 컨테이너가 없다.
  assert.ok(!src.includes("max-w-[340px]"), "제거된 디버그 컨테이너의 흔적이 남아 있다");
  assert.ok(!src.includes("max-h-[88px]"), "제거된 88px 디버그 썸네일의 흔적이 남아 있다");
});

test("미리보기: 기존 packed-alpha 컴포지터 배선은 그대로다 (건드리지 않음)", () => {
  const src = read(PREVIEW);
  assert.match(src, /breathingDeliveryFormat/);
  assert.match(src, /deliveryFormat=\{breathingDeliveryFormat\}/);
  assert.match(src, /hasIdle && resolveDeliveryFormat\(pipeline\) === "packed_alpha"/);
  assert.match(src, /shouldRenderThemeBackdrop\(bakedAsset\)/);
  assert.match(src, /shouldApplySubjectTransform\(bakedAsset\)/);
  assert.match(src, /playbackFrameClass\(bakedAsset\)/);
});

test("미리보기: 라이브러리 상태 계약(applyLibraryOverride/isLibraryFlow)은 그대로다 (건드리지 않음)", () => {
  const src = read(PREVIEW);
  assert.match(src, /isLibraryFlow\?: boolean;/);
  assert.match(src, /libraryPublication\?: LibraryPublication \| null;/);
  assert.match(
    src,
    /applyLibraryOverride\(readStoredPipeline\(\), isLibraryFlow, libraryPublication\)/
  );
});

// ── My Library: 단계 그래프가 Theme → Composer(PreviewScreen) 로 직행한다 ───
//
// "Composer" 는 이 코드베이스에 별도로 존재하지 않는다 — PreviewScreen 이
// 유일한 웹 컴포지터이고, 이 화면이 그 자리다. 아래는 그 사실과, 테마 확정이
// 중간 단계 없이 바로 preview(=Composer)로 넘어간다는 것을 고정한다.

test("My Library: 단계 타입에 레거시/중간 단계가 없다 — library(단일 그리드) → themes → preview 뿐이다", () => {
  const src = read(MY_LIBRARY);
  assert.match(
    src,
    /type LibraryStep = "library" \| "themes" \| "preview";/,
    "새 중간 단계가 추가됐거나 이름이 바뀌었다"
  );
});

test("My Library: 테마 확정(onContinue)이 다른 단계를 거치지 않고 바로 preview 로 넘어간다", () => {
  const src = read(MY_LIBRARY);
  const onContinueIdx = src.indexOf("onContinue={(themeId) => {");
  assert.ok(onContinueIdx > 0, "onContinue 콜백을 찾을 수 없다");
  const body = src.slice(onContinueIdx, src.indexOf("}}", onContinueIdx));
  assert.match(body, /setStep\("preview"\)/, "테마 확정이 preview(Composer) 로 가지 않는다");
  assert.ok(!/setStep\("confirm"\)|setStep\("device"\)|setStep\("play"\)/.test(body), "중간 단계로 새는 경로가 생겼다");
});

test("My Library: preview 단계는 PreviewScreen(Composer) 하나만 렌더한다 — 다른 프리뷰가 끼어들지 않는다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  assert.ok(stepBlockStart > 0, "preview 단계 분기를 찾을 수 없다");
  const stepBlockEnd = src.indexOf('if (step === "themes"', stepBlockStart);
  const block = src.slice(stepBlockStart, stepBlockEnd);
  assert.match(block, /<PreviewScreen/, "preview 단계가 PreviewScreen 을 렌더하지 않는다");
  assert.equal(
    [...block.matchAll(/<[A-Z]\w*Screen/g)].length,
    1,
    "preview 단계에 화면 컴포넌트가 하나가 아니다 — 병행 프리뷰가 생겼다"
  );
});

test("My Library: Composer 에 선택 테마·발행 메타데이터·cutoutImage=null 이 모두 흐른다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const previewCallIdx = src.indexOf("<PreviewScreen", stepBlockStart);
  const previewCall = src.slice(previewCallIdx, src.indexOf("onBack", previewCallIdx));
  assert.match(previewCall, /cutoutImage=\{null\}/);
  assert.match(previewCall, /\bisLibraryFlow\b/);
  assert.match(previewCall, /libraryPublication=\{libraryPublication\}/);
  assert.match(previewCall, /selectedTheme=\{selectedTheme\}/, "선택한 테마가 Composer 로 흐르지 않는다");
});

test("My Library: Play on Beam(onPlayOnBeam)이 preview 단계 콜백 안에서 theme_play → pet_asset 순서로 나간다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const stepBlockEnd = src.indexOf('if (step === "themes"', stepBlockStart);
  const block = src.slice(stepBlockStart, stepBlockEnd);
  const themeIdx = block.indexOf('event: "theme_play"');
  const petIdx = block.indexOf('event: "pet_asset"');
  assert.ok(themeIdx > 0 && petIdx > 0 && themeIdx < petIdx, "Play on Beam 순서가 preview 단계 밖에 있거나 뒤바뀌었다");
  assert.match(block, /pet_id:\s*selectedPet\.petId/);
  assert.match(block, /motion_id:\s*selectedMotion\.motionId/);
  assert.match(block, /spawn_vfx:\s*"heart"/);
});

// ── 실제 버그 재현: 백엔드 delivery_format=null → PreviewScreen 까지 추적 ────
//
// 확인된 실제 결함: 이 delivery_format 컬럼이 생기기 전에 발행된 펫은
// 백엔드가 null 을 돌려주고, 그 null 이 그대로 pipeline.delivery_format 까지
// 흘러 IdleLoopVideo 를 packed 대신 blackkey 로 떨어뜨렸다 — 실제 자산은
// packed_alpha 인데도 raw vstack(RGB+알파 매트)이 합성 없이 보였다.
//
// my-library-api.ts 가 원본에서 이미 packed_alpha 로 채우므로, 그 결과가
// applyLibraryOverride 를 거쳐도 유지되는지 **실제 함수를 연결해** 확인한다
// (문자열 매칭이 아니라 진짜 데이터 흐름).

test("실제 버그 재현: delivery_format=null 백엔드 응답도 PreviewScreen 의 pipeline 에는 packed_alpha 로 도착한다", async () => {
  const pets = await fetchMyLibraryPets({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) =>
      url.endsWith("/registry/mine")
        ? ({ ok: true, status: 200, json: async () => ({ pets: [{ pet_id: "pet_1", content_id: "content-1" }] }) } as Response)
        : ({
            ok: true,
            status: 200,
            json: async () => ({
              pet_id: "pet_1",
              motion_id: "BREATHING",
              url: "https://signed.test/breathing.mp4",
              // 실제 버그를 그대로 재현한다 — 낡은 레코드는 이 필드가 없다.
              delivery_format: null,
              background_baked: false,
            }),
          } as Response),
  });

  const pet = pets[0];
  const motion = pet?.motions[0];
  assert.ok(pet && motion, "라이브러리 fetch 결과가 없다");

  // MyLibraryScreen.toLibraryPublication 과 같은 모양 — 모션을 고른 시점에
  // 동기적으로 만들어지는 값이다.
  const libraryPublication: LibraryPublication = {
    petId: pet.petId,
    idleVideoUrl: motion.url,
    deliveryFormat: motion.deliveryFormat,
    backgroundBaked: motion.backgroundBaked,
  };

  const pipeline = applyLibraryOverride(null, true, libraryPublication);

  assert.equal(
    pipeline?.delivery_format,
    "packed_alpha",
    "delivery_format=null 이 PreviewScreen 의 pipeline 까지 null 로 새어 들어갔다"
  );
});

// ── Play on Beam 후에도 Composer 에 머문다 (Phase 6 재정리) ──────────────────
//
// 예전에는 my-library-screen 이 직접 두 sendDeviceCommand 호출의 결과를
// 무시하고 무조건 "Sent to Beam" 을 띄웠다(하나만 실패해도 성공 배지가 떴다).
// Phase 6 에서는 명령 구성은 여전히 MyLibraryScreen 이 갖되(onPlayOnBeam
// prop), 전송 상태(Sending/Sent/Error)는 PreviewScreen(Composer)이 소유하고
// 결과의 ok 를 반드시 확인한다. 콜백은 여전히 onComplete()/setStep 을 부르지
// 않는다 — Composer 에 머문다.

test("Play on Beam: theme_play 가 먼저, pet_asset 이 다음 — 둘 다 현재 선택값을 쓴다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const onPlayOnBeamStart = src.indexOf("onPlayOnBeam={async () => {", stepBlockStart);
  assert.ok(onPlayOnBeamStart > 0, "preview 단계의 onPlayOnBeam 콜백을 찾을 수 없다");
  const onChangeMotionStart = src.indexOf("onChangeMotion={", onPlayOnBeamStart);
  const callback = src.slice(onPlayOnBeamStart, onChangeMotionStart);

  const themeIdx = callback.indexOf('event: "theme_play"');
  const petIdx = callback.indexOf('event: "pet_asset"');
  assert.ok(themeIdx > 0 && petIdx > 0 && themeIdx < petIdx, "theme_play 가 pet_asset 보다 먼저 나가지 않는다");
  assert.match(callback, /theme_id:\s*currentTheme\.themeKey/, "현재 선택된 테마를 쓰지 않는다");
  assert.match(callback, /pet_id:\s*selectedPet\.petId/, "현재 선택된 펫을 쓰지 않는다");
  assert.match(callback, /motion_id:\s*selectedMotion\.motionId/, "현재 선택된 모션을 쓰지 않는다");
  assert.match(callback, /spawn_vfx:\s*"heart"/, "로컬 VFX 선택자를 보내지 않는다");
});

test("Play on Beam: 두 명령의 결과를 모두 확인한 뒤에만 ok:true 를 돌려준다 — 결과를 무시하지 않는다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const onPlayOnBeamStart = src.indexOf("onPlayOnBeam={async () => {", stepBlockStart);
  const onChangeMotionStart = src.indexOf("onChangeMotion={", onPlayOnBeamStart);
  const callback = src.slice(onPlayOnBeamStart, onChangeMotionStart);

  assert.match(
    callback,
    /if \(!themeResult\.ok\) return \{ ok: false/,
    "theme_play 실패를 확인하지 않고 넘어간다 — 예전 결함(무조건 성공 취급)이 되살아났다"
  );
  assert.match(
    callback,
    /petResult\.ok\s*\n\s*\? \{ ok: true, commandId: petResult\.command_id \}\s*\n\s*: \{ ok: false/,
    "pet_asset 결과를 확인하지 않는다 — 하나만 실패해도 ok:true 가 나갈 수 있다"
  );
});

test("Play on Beam: 콜백이 상위 onComplete() 를 부르지 않고 다른 단계로도 넘어가지 않는다 — Composer 에 머문다", () => {
  const src = read(MY_LIBRARY);
  const stepBlockStart = src.indexOf('if (step === "preview"');
  const onPlayOnBeamStart = src.indexOf("onPlayOnBeam={async () => {", stepBlockStart);
  const onChangeMotionStart = src.indexOf("onChangeMotion={", onPlayOnBeamStart);
  const callback = src.slice(onPlayOnBeamStart, onChangeMotionStart);

  assert.ok(!/\bonComplete\(\);/.test(callback), "onPlayOnBeam 콜백이 상위 onComplete() 를 불러 화면을 떠난다");
  assert.ok(!/setStep\(/.test(callback), "onPlayOnBeam 콜백이 다른 단계로 넘어간다 — Composer 를 떠난다");
});

test("Play on Beam: 전송 상태(Sending/Sent/Error)는 이제 Composer(PreviewScreen)가 소유한다", () => {
  const myLibrarySrc = read(MY_LIBRARY);
  assert.ok(
    !myLibrarySrc.includes("deviceSentAt"),
    "my-library-screen 이 여전히 자체 전송-상태를 갖고 있다 — 소유권이 Composer 로 옮겨오지 않았다"
  );
  assert.ok(
    !/Sent to Beam/.test(myLibrarySrc),
    "my-library-screen 에 예전 절대 위치 'Sent to Beam' pill 잔재가 남아 있다"
  );

  const previewSrc = read(PREVIEW);
  assert.match(
    previewSrc,
    /useState<"idle" \| "sending" \| "sent" \| "acked" \| "error">\("idle"\)/,
    "PreviewScreen 이 beamStatus 상태를 소유하지 않는다"
  );
  assert.match(previewSrc, /onPlayOnBeam\?:\s*\(\)\s*=>\s*Promise/, "onPlayOnBeam prop 계약이 없다");
  assert.match(
    previewSrc,
    /if \(result\.ok\) \{\s*\n\s*setBeamStatus\("sent"\);/,
    "결과의 ok 를 확인하지 않고 상태를 정한다"
  );
});

test("Play on Beam: 재전송을 막는 가드가 없다 — sent/error 이후에도 다시 누르면 다시 보낸다", () => {
  const previewSrc = read(PREVIEW);
  const start = previewSrc.indexOf("const handlePlayOnBeam = useCallback(async () => {");
  assert.ok(start > 0, "handlePlayOnBeam 을 찾을 수 없다");
  const end = previewSrc.indexOf("}, [onPlayOnBeam, beamStatus, pollForAck]);", start);
  const body = previewSrc.slice(start, end);
  assert.match(body, /beamStatus === "sending"/);
  assert.ok(!/beamStatus === "sent"/.test(body), "sent 이후 재전송을 막는 가드가 생겼다");
});

test("Play on Beam: 모션 변경 경로는 여전히 열려 있다 — themes 화면의 onBack 이 그리드(library)로 간다", () => {
  const src = read(MY_LIBRARY);
  assert.match(src, /onBack=\{\(\) => setStep\("library"\)\}/, "테마 선택 화면에서 그리드로 돌아가는 경로가 사라졌다");
});

test("Change Motion: 발행 모션이 둘 이상일 때만 onChangeMotion 을 내려준다 — 없는 인터랙션을 약속하지 않는다", () => {
  const src = read(MY_LIBRARY);
  assert.match(
    src,
    /\.motions\.length > 1\s*\n\s*\? \(\) => setStep\("library"\)\s*\n\s*: undefined/,
    "onChangeMotion 이 모션 개수와 무관하게 항상 넘어간다"
  );
});

test("업로드 흐름은 그대로다 — 이번 변경이 preview-screen.tsx 를 건드리지 않았다", () => {
  // PreviewScreen 자체는 "머무르기" 를 모른다 — 그 판단은 전부 My Library 쪽
  // onComplete 콜백에 있다. 업로드 흐름(EternalBeamApp.tsx)이 쓰는 PreviewScreen
  // 의 handleConfirm 은 그대로 hasIdle 일 때 onComplete() 를 부르고 끝난다.
  const src = read(PREVIEW);
  assert.match(
    src,
    /if \(hasIdle\) \{\s*\n\s*onComplete\(\);\s*\n\s*return;\s*\n\s*\}/,
    "PreviewScreen 의 handleConfirm 이 바뀌어 업로드 흐름에 영향을 줄 수 있다"
  );
});
