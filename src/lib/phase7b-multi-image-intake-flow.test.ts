import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../app/EternalBeamApp.tsx", import.meta.url), "utf8");
const upload = readFileSync(
  new URL("../components/memorial/photo-upload-screen.tsx", import.meta.url),
  "utf8",
);
const processing = readFileSync(
  new URL("../components/memorial/ai-processing-screen.tsx", import.meta.url),
  "utf8",
);
const trigger = readFileSync(
  new URL("../components/memorial/media-file-trigger.tsx", import.meta.url),
  "utf8",
);
const slotState = readFileSync(new URL("./pet-slot-state.ts", import.meta.url), "utf8");
const intakeSession = readFileSync(
  new URL("./phase1-intake-session.ts", import.meta.url),
  "utf8",
);
const pending = readFileSync(new URL("./pending-generation.ts", import.meta.url), "utf8");

test("upload UI accepts up to 3 images and shows per-image removal/status surface", () => {
  assert.match(upload, /maxImages = 3/);
  assert.match(upload, /onFiles=\{ingestFiles\}/);
  assert.match(upload, /multiple/);
  // Phase 3 — the room-to-fill check moved into the screen itself (with
  // classifyIntakeBatch/clampToRoom) so an over-the-limit pick can show an
  // inline reason instead of the parent silently truncating the selection.
  assert.match(upload, /const room = Math\.max\(0, maxImages - \(isVideo \? 0 : selectedImages\.length\)\)/);
  assert.match(upload, /clampToRoom\(images, room\)/);
  assert.match(upload, /selectedImages\.map\(/);
  assert.match(upload, /onClick=\{\(\) => onRemoveImage\(index\)\}/);
  assert.match(upload, /statusLabel\(state\)/);
  assert.match(upload, /disabled=\{!isStartEnabled\}/);
});

test("upload UI validates picks and lets a photo be replaced without a new pipeline", () => {
  assert.match(upload, /classifyIntakeBatch\(files\)/);
  assert.match(upload, /onReplaceImage\?: \(index: number, file: File\) => void/);
  assert.match(upload, /onReplaceImage\(index, images\[0\]\)/);
  // 교체는 새 인테이크가 아니다 — beginPhase1Intake/clearPhase1Intake 를
  // 이 화면에서 부르지 않는다(신원은 부모의 슬롯 앞으로만 발급된다).
  assert.ok(!/beginPhase1Intake|clearPhase1Intake/.test(upload));
});

test("shared file trigger supports multi-select without breaking single-file callers", () => {
  assert.match(trigger, /onFile\?: \(file: File\) => void/);
  assert.match(trigger, /onFiles\?: \(files: File\[\]\) => void/);
  assert.match(trigger, /multiple\?: boolean/);
  assert.match(trigger, /if \(onFiles\) onFiles\(files\);\s*else if \(onFile\) onFile\(files\[0\]\)/s);
});

test("app wires multi-image state into upload + processing screens", () => {
  assert.match(app, /const activePetSlot = petSlots\[activePetSlotIndex\]/);
  assert.match(app, /const uploadedImages = activePetSlot\.uploadedImages/);
  // 사진이 있어야만 인테이크가 시작된다 — 영상 한 편으로 Start 를 열면
  // aiProcessing 이 처리할 게 없어 그대로 멈춘다.
  assert.match(app, /const canStartIntake = uploadedImages\.length > 0\n/);
  assert.match(app, /const intakeImageStates = activePetSlot\.intakeImageStates/);
  assert.match(app, /canStart=\{canStartIntake\}/);
  assert.match(app, /onImagesUpload=\{handleImagesUpload\}/);
  assert.match(app, /onRemoveImage=\{handleRemoveUploadedImage\}/);
  assert.match(app, /imageStates=\{intakeImageStates\}/);
  assert.match(app, /onImageStateChange=\{\(index, state\) =>\s*handleIntakeImageStateForSlot\(activePetSlotIndex, index, state\)/s);
});

test("a video can never enter the image-only processing path", () => {
  // 업로드 화면의 Start 는 사진이 있을 때만 열리고, 진입 핸들러가 한 번 더 막는다.
  assert.match(app, /if \(!canStartIntake\) return\s*\n\s*navigateTo\('aiProcessing'\)/);
  // 영상은 홈 화면 선택기와 같은 길(themeSelection)로 간다.
  assert.match(app, /if \(kind === 'video'\) navigateTo\('themeSelection'\)/);
  assert.match(upload, /disabled=\{!isStartEnabled\}/);
});

test("media type is per pet, never read from global localStorage", () => {
  assert.match(app, /mediaType=\{activeMediaKind\}/);
  assert.match(app, /const activeMediaKind = activePetSlot\.mediaKind/);
  assert.match(upload, /mediaType\?: MediaKind \| null;/);
  assert.ok(
    !/localStorage/.test(upload),
    "the upload screen must not read app-wide storage for the active pet's media",
  );
  assert.ok(!/setMediaType\(/.test(upload), "media type must not live in screen-local state");
});

test("Start enablement follows active slot images: 0 disabled, 1-3 enabled, slot switch isolated", () => {
  assert.match(upload, /const isStartEnabled = canStart \?\? selectedImages\.length > 0/);
  assert.match(app, /uploadedImages = activePetSlot\.uploadedImages/);
  // 첫 장은 배열에서 **파생**된다 — 따로 들고 있던 uploadedImage 칸은 없앴다.
  assert.match(app, /const uploadedImage = slotPrimaryMedia\(activePetSlot\)/);
  assert.match(app, /return slot\.uploadedImages\[0\] \?\? null/);
  assert.ok(!/uploadedImage: string \| null\n  uploadedImages/.test(app));
  assert.match(app, /onSelectPetSlot=\{handleSelectPetFromUpload\}/);
  assert.match(app, /uploadedImages: nextImages,/);
});

test("a second selection appends up to 3 photos instead of replacing them", () => {
  const handler = app.slice(
    app.indexOf("const handleImagesUpload"),
    app.indexOf("const handleRemoveUploadedImage"),
  );
  assert.match(handler, /const room = MAX_IMAGES_PER_PET - existing\.length/);
  assert.match(handler, /files\.slice\(0, room\)/);
  assert.match(handler, /\[\.\.\.base, \.\.\.urls\]\.slice\(0, MAX_IMAGES_PER_PET\)/);
  // 같은 펫에 장을 더하는 것이므로 업로드 신원은 새로 발급하지 않는다. 그 규칙은
  // identityForAddedPhotos 로 옮겨졌다(빈 자리는 언제나 새 신원 — 동작은
  // phase1-intake-session.test.ts 가 검증한다).
  assert.match(
    handler,
    /const identity = identityForAddedPhotos\(slotId, existing\.length, slot\.intakeIdentity\)/,
  );
  assert.match(
    intakeSession,
    /if \(existingPhotoCount > 0\) \{\s*return current \?\? readPhase1Intake\(slotId\) \?\? beginPhase1Intake\(slotId, createContentId\);\s*\}\s*return beginPhase1Intake\(slotId, createContentId\);/,
  );
  assert.equal(app.split("const MAX_IMAGES_PER_PET = 3").length - 1, 1);
});

test("pet intake state is isolated by slot with a max of 3 pets", () => {
  assert.match(app, /type PetIntakeSlot = \{/);
  assert.match(slotState, /export const MAX_PET_SLOTS = 3/);
  assert.match(app, /const \[petSlots, setPetSlots\] = useState<PetIntakeSlot\[]>/);
  // 시작 자리는 복원된 커서에서 오되, 실제 자리 수를 넘지 않는다.
  assert.match(app, /const \[activePetSlotIndex, setActivePetSlotIndex\] = useState\(\(\) =>/);
  assert.match(app, /onAddPetSlot=\{handleAddPetFromUpload\}/);
  assert.match(app, /onSelectPetSlot=\{handleSelectPetFromUpload\}/);
});

test("adding a pet is decided outside the state updater, by seat number", () => {
  const add = app.slice(app.indexOf("const addPetSlot"), app.indexOf("/**\n   * 업로드 확정"));
  assert.match(add, /if \(petSlots\.length >= MAX_PET_SLOTS\)/);
  assert.match(add, /const nextIndex = petSlots\.length/);
  assert.match(add, /createPetIntakeSlot\(nextIndex\)/);
  // 새 자리는 빈 자리다 — 앞선 세션의 보관분을 물려받지 않는다.
  assert.match(add, /clearPetSlotState\(nextSlot\.slotId\)/);
  assert.match(add, /switchPetSlotState\(activePetSlotId, nextSlot\.slotId\)/);
  // 갱신 함수 안에서 바깥 변수를 적던 경로는 사라졌다.
  assert.ok(!/nextIndex = next\.length - 1/.test(app));
  assert.match(slotState, /export function petSlotIdForIndex\(index: number\): string \{\s*return `pet_slot_\$\{index \+ 1\}`/s);
});

test("switching pets swaps the pipeline/cutout projection, so generation follows the active pet", () => {
  assert.match(app, /switchPetSlotState\(activePetSlotId, petSlotIdForIndex\(index\)\)/);
  assert.match(slotState, /"eternal_beam_pipeline_v1"/);
  assert.match(slotState, /"eternal_beam_pending_cutout_v1"/);
  assert.match(slotState, /"eternal_beam_content_id"/);
  assert.match(slotState, /"eternal_beam_current_content_id"/);
  // 보관된 값이 없는 칸은 비운다 — 남기면 그게 직전 펫의 잔상이다.
  assert.match(slotState, /writeRaw\(scoped, snapshot\[scoped\.key\] \?\? null\)/);
  // 대기 누끼 File 은 메모리에만 있어 저장소 투영으로 옮겨지지 않는다.
  assert.match(slotState, /activatePendingCutoutSlot\(slotId\)/);
  assert.match(processing, /petSlotId,\n        \);/);
});

test("state writes never happen inside a React state updater", () => {
  const updater = app.slice(app.indexOf("const updatePetSlot"), app.indexOf("const projectedMediaRef"));
  for (const forbidden of ["commitMainMedia(", "clearPhase1Intake(", "localStorage.", "sessionStorage."]) {
    assert.ok(!updater.includes(forbidden), `${forbidden} must not run inside updatePetSlot`);
  }
  const remove = app.slice(
    app.indexOf("const handleRemoveUploadedImage"),
    app.indexOf("const handleIntakeImageStateForSlot"),
  );
  const body = remove.slice(remove.indexOf("updatePetSlot("));
  assert.ok(!body.includes("clearPhase1Intake("));
  assert.ok(!body.includes("commitMainMedia("));
});

test("processing uploads each selected image via existing Phase 1 endpoint then builds one identity profile", () => {
  assert.match(processing, /for \(let index = 0; index < total; index \+= 1\)/);
  assert.match(processing, /const original = await persistPhase1Intake\(/);
  assert.match(processing, /const ready = await persistPhase1Intake\(/);
  assert.match(processing, /ready\.referenceId !== original\.referenceId/);
  assert.match(processing, /onImageStateChangeRef\.current\?\.\(index, \{ status: "error"/);
  assert.match(processing, /await buildIdentityProfile\(firstReady\.petId, auth\.token\)/);
  assert.match(processing, /const hasFailures = failedCount > 0 \|\| successCount !== total/);
  assert.match(processing, /if \(hasFailures\) \{\s*setStatusLine\(t\.someUploadsFailed\(failedCount, total\)\);/s);
});

test("each image is verified against its OWN original+cutout pair, never a sibling's", () => {
  // 장별 검증은 이번 장의 응답만 믿는다 — 앞 장이 준비됐다는 사실이
  // 뒷 장의 통과 사유가 되면 안 된다.
  assert.match(processing, /cutoutFile: cutout\.cutFile/);
  assert.match(
    processing,
    /!ready\.intakeReady \|\|\s*!ready\.referenceId \|\|\s*!ready\.cutoutReferenceId \|\|\s*ready\.referenceId !== original\.referenceId/s,
  );
});

test("identity build runs once, after the per-image loop has finished", () => {
  const loopEnd = processing.indexOf("dumpImageTrace()");
  const build = processing.indexOf("await buildIdentityProfile(");
  assert.ok(loopEnd > 0 && build > loopEnd, "identity build must follow the intake loop");
  assert.equal(processing.split("await buildIdentityProfile(").length - 1, 1);
});

test("session receipt carries all ready references, not just the first pair", () => {
  // 세션 페이로드가 firstReady 한 쌍만 싣던 결함의 회귀 가드.
  assert.match(processing, /const readyPairs: ReadyIntakePair\[\] = \[\]/);
  assert.match(
    processing,
    /readyPairs\.push\(\{\s*petId: ready\.petId,\s*referenceId: ready\.referenceId,\s*cutoutReferenceId: ready\.cutoutReferenceId,\s*\}\)/s,
  );
  assert.match(
    processing,
    /const intakeReceipt = buildPhase1IntakeReceipt\(firstReady\.petId, readyPairs\)/,
  );
  assert.match(processing, /phase1_intake: intakeReceipt,/);
  // 단수 필드를 직접 쓰던 옛 경로는 남아 있지 않다.
  assert.ok(!/original_reference_id: firstReady\.referenceId/.test(processing));
  // StoredPipeline 은 단수 필드를 유지한 채 배열을 더한다.
  assert.match(processing, /original_reference_id: string;/);
  assert.match(processing, /original_reference_ids\?: string\[\];/);
  assert.match(processing, /cutout_reference_ids\?: string\[\];/);
});

test("app rehydrates pets 1-3 from the per-pet archive on startup", () => {
  const restore = app.slice(app.indexOf("function restorePetSlots"), app.indexOf("type Screen ="));
  // 자리 수는 MAX_PET_SLOTS 로 묶인다 — 넷째 자리는 복원으로도 생기지 않는다.
  assert.match(restore, /for \(let index = 0; index < MAX_PET_SLOTS; index \+= 1\)/);
  // 자리는 이어져 있다 — 중간이 비면 거기서 멈춘다.
  assert.match(restore, /if \(!persisted\) break/);
  // 각 자리는 **자기 자리의** 스냅샷·신원·테마만 읽는다.
  assert.match(restore, /readPetSlotSnapshot\(slotId, isActive\)/);
  assert.match(restore, /intakeIdentity: readPhase1Intake\(slotId\)/);
  assert.match(restore, /readPetSlotValue\(slotId, 'eternal_beam_theme_id', isActive\)/);
  // 일부만 돌아온 사진은 쓰지 않는다 — 장 번호가 영수증과 어긋난다.
  assert.match(restore, /persisted\.imageUrls\.length === persisted\.imageCount/);
  assert.match(app, /const rehydrated = restorePetSlots\(restoredActiveIndex\)/);
  assert.match(app, /if \(rehydrated\.length > 0\) return rehydrated/);
  assert.match(
    app,
    /useState\(\(\) =>\s*Math\.min\(restoredActiveIndex, Math\.max\(0, petSlots\.length - 1\)\)/s,
  );
});

test("the active pet's screen state is written to its own seat on every change", () => {
  assert.match(app, /writeActivePetSlotSnapshot\(serialized\)/);
  assert.match(app, /capturePetSlotState\(activePetSlotId\)/);
  assert.match(app, /writeActivePetSlotIndex\(index\)/);
  assert.match(app, /writeActivePetSlotIndex\(nextIndex\)/);
  // 스냅샷은 **내용이 아니라 주소**만 담는다 — data:/blob: 은 담지 않는다.
  const serialize = app.slice(
    app.indexOf("function restorableMediaUrl"),
    app.indexOf("function parsePetSlot"),
  );
  assert.match(serialize, /if \(\/\^https\?:\\\/\\\/\/i\.test\(value\)\) return value/);
  assert.match(serialize, /imageCount: images\.length/);
  assert.match(serialize, /urls\.every\(\(url\): url is string => Boolean\(url\)\) \? urls : \[\]/);
  assert.ok(!/uploadedImages: slot\.uploadedImages/.test(serialize), "사진 내용을 그대로 담고 있다");
  assert.ok(!/cutoutImage: slot\.cutoutImage/.test(serialize), "누끼 내용을 그대로 담고 있다");
});

test("replacing a seat clears only that seat's archive and identity", () => {
  const add = app.slice(app.indexOf("const addPetSlot"), app.indexOf("/**\n   * 업로드 확정"));
  assert.match(add, /clearPetSlotState\(nextSlot\.slotId\)/);
  assert.match(add, /clearPhase1Intake\(nextSlot\.slotId\)/);
  // 전체를 지우는 함수는 리셋·로그아웃에서만 쓴다.
  assert.ok(!/clearAllPetSlotState\(\)/.test(add));
  assert.match(slotState, /export function clearPetSlotState\(slotId: string\): void \{[^}]*delete archive\[slotId\]/s);
});

test("the pipeline snapshot type covers the fields callers actually read", () => {
  assert.match(pending, /export interface StoredPipelineSnapshot \{/);
  assert.match(pending, /export function readStoredPipeline\(\): StoredPipelineSnapshot \| null/);
  for (const field of ["content_id", "idle_video_url", "action_video_url", "scene_id"]) {
    assert.match(pending, new RegExp(`${field}\\?:`), `${field} 가 타입에 없다`);
  }
  // 런타임은 그대로다 — 여전히 파싱만 한다.
  assert.match(pending, /JSON\.parse\(raw\) as StoredPipelineSnapshot/);
});

test("theme selection lives on the pet, not on the app", () => {
  assert.match(app, /selectedTheme: number \| null\n\}/);
  assert.match(app, /const selectedTheme = activePetSlot\.selectedTheme/);
  assert.match(
    app,
    /const setSelectedTheme = \(themeId: number \| null\) => \{\s*updatePetSlot\(activePetSlotIndex, \(slot\) => \(\{ \.\.\.slot, selectedTheme: themeId \}\)\)/s,
  );
  // 앱 전역 state 로 되돌아가는 길은 닫혀 있다.
  assert.ok(!/useState<number \| null>\(\(\) =>\s*getMemorialThemeByKey/.test(app));
});

test("per-pet theme, NFC, scene, video and binding keys are registered", () => {
  for (const key of [
    "eternal_beam_theme_key",
    "eternal_beam_theme_id",
    "eternal_beam_selected_theme_id",
    "eternal_beam_background_theme_id",
    "eternal_beam_background_theme_name",
    "eternal_beam_custom_bg_video_url",
    "eternal_beam_nfc_payload",
    "eternal_beam_canonical_scene_v1",
    "eternal_beam_current_video_id",
    "eternal_beam_hologram_video_id",
    "eternal_beam_pet_id",
    "eternal_beam_pet_binding",
    "eternal_beam_pet_name",
  ]) {
    assert.ok(slotState.includes(`"${key}"`), `${key} 가 ACTIVE_PET_KEYS 에 없다`);
  }
  // 원본 미디어 칸은 등록되되 보관되지 않는다.
  assert.match(slotState, /"eternal_beam_main_photo", scope: "local", archive: false/);
  assert.match(slotState, /const ARCHIVE_VALUE_LIMIT = 256 \* 1024/);
});
