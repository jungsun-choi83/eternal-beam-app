/**
 * 멀티 포토 제출 배선 — "활성 펫의 사진 **전부**가 제출된다"를 소스로 고정한다.
 *
 * 동작 자체는 컴포넌트 테스트가 검증한다:
 *   - ai-processing-screen.test.tsx  (1/2/3장 → beginIntakePass + 업로드 루프)
 *   - photo-upload-screen.test.tsx   (한 펫에 사진 더하기, 펫 사이의 격리)
 * 여기서는 EternalBeamApp 이 그 두 화면을 **펫의 배열 전체**로 잇는다는 것,
 * 그리고 "지금 보이는 한 장"이 제출 대상을 정하지 않는다는 것을 고정한다.
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const app = read("../app/EternalBeamApp.tsx");
const upload = read("../components/memorial/photo-upload-screen.tsx");
const processing = read("../components/memorial/ai-processing-screen.tsx");

test("each pet slot owns a photo array, and both screens receive the active pet's whole array", () => {
  assert.match(app, /type PetIntakeSlot = \{[\s\S]*?uploadedImages: string\[\]/);
  assert.match(app, /const uploadedImages = activePetSlot\.uploadedImages/);
  // 업로드 화면과 처리 화면 둘 다 같은 배열을 받는다.
  assert.equal(app.split("uploadedImages={uploadedImages}").length - 1, 2);
});

test("the single displayed photo is derived for display only", () => {
  // 대표 사진은 배열의 첫 장에서 파생된 값이다 — 따로 저장되는 "선택된 사진" 상태가 없다.
  assert.match(app, /const uploadedImage = slotPrimaryMedia\(activePetSlot\)/);
  assert.ok(!/activeImageIndex|selectedImageIndex|highlightedPhoto|selectedPhotoIndex/.test(app + upload + processing));
});

test("the processing screen submits the array, not the displayed photo", () => {
  // 업로드 루프: 배열(uploadedImages)이 있으면 그것 전체. 단일 값은 배열이 없을 때의 폴백일 뿐.
  assert.match(
    processing,
    /uploadedImages && uploadedImages\.length > 0\s*\? uploadedImages\.filter\(\(url\) => url\.startsWith\("data:image\/"\)\)/,
  );
  assert.match(processing, /const total = intakeImages\.length/);
  // 동기화에 가는 목록도 배열 전체다.
  assert.match(
    processing,
    /const currentPhotos = useMemo\(\s*\(\) =>\s*uploadedImages && uploadedImages\.length > 0\s*\? uploadedImages/,
  );
  assert.match(processing, /photos: currentPhotosRef\.current,/);
});

test("adding photos appends to the active pet only; switching pets does not touch any array", () => {
  const add = app.slice(app.indexOf("const handleImagesUpload"), app.indexOf("const handleRemoveUploadedImage"));
  // 호출 시점의 활성 펫 번호를 잡아 두고(파일을 읽는 동안 펫을 바꿔도 그 펫에 붙는다) 그 자리만 갱신한다.
  assert.match(add, /const slotIndex = activePetSlotIndex/);
  assert.match(add, /updatePetSlot\(slotIndex, \(current\) => \{/);
  assert.match(add, /\[\.\.\.base, \.\.\.urls\]\.slice\(0, MAX_IMAGES_PER_PET\)/);
  assert.match(app, /prev\.map\(\(slot, i\) => \(i === index \? updater\(slot\) : slot\)\)/);

  const selectStart = app.indexOf("const selectPetSlot = ");
  const select = app.slice(selectStart, app.indexOf("\n  }\n", selectStart));
  assert.match(select, /switchPetSlotState\(activePetSlotId, petSlotIdForIndex\(index\)\)/);
  assert.ok(!/setPetSlots|updatePetSlot/.test(select), "switching pets must not rewrite any pet's photos");
  assert.match(select, /setActivePetSlotIndex\(index\)/);
});

test("the upload screen offers a same-pet 'add photo' control next to the thumbnails", () => {
  const tile = upload.slice(upload.indexOf("pet-intake__add-photo") - 400, upload.indexOf("pet-intake__add-photo") + 500);
  assert.match(tile, /!photosLocked && selectedImages\.length < maxImages/);
  // 큰 선택 영역과 같은 경로 — 활성 펫의 배열에 붙는다. 펫을 추가하지 않는다.
  assert.match(tile, /onFiles=\{ingestFiles\}/);
  assert.ok(!/onAddPetSlot/.test(tile));
  assert.match(upload, /if \(onImagesUpload\) \{\s*onImagesUpload\(accepted\);/);
});
