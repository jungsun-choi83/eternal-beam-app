/**
 * 펫별 저장소 칸 등록 커버리지.
 *
 * 인테이크 흐름이 쓰는 `eternal_beam_*` 키를 **전부** 훑어, 하나도 빠짐없이
 * 아래 네 분류 중 하나에 들어 있는지 본다. 새 키를 분류 없이 들여오면 이
 * 테스트가 깨진다 — 그 순간이 "이 값은 펫마다 다른가?"를 묻는 자리다.
 *
 * 묻지 않고 지나가면 어떻게 되는지는 이미 겪었다: 파이프라인·대기 누끼·
 * content_id 가 앱 전체에 한 칸씩이던 동안, 펫 2를 처리하면 펫 1의 미리보기와
 * 생성이 펫 2를 썼다.
 */
import { strict as assert } from "node:assert";
import { readFileSync, readdirSync } from "node:fs";
import { test } from "node:test";

import {
  ACTIVE_PET_STORAGE_KEYS,
  ARCHIVED_PET_STORAGE_KEYS,
} from "./pet-slot-state.ts";

const LIB_DIR = new URL("./", import.meta.url);
const MEMORIAL_DIR = new URL("../components/memorial/", import.meta.url);

/** 펫마다 답이 달라지고, **자리별로 갈아 끼워진다**(ACTIVE_PET_KEYS 등록분). */
const PER_PET_SCOPED = [
  "eternal_beam_pipeline_v1",
  "eternal_beam_pending_cutout_v1",
  "eternal_beam_pet_slot_snapshot_v1",
  "eternal_beam_content_id",
  "eternal_beam_current_content_id",
  "eternal_beam_main_photo",
  "eternal_beam_main_video_url",
  "eternal_beam_media_type",
  "eternal_beam_pet_id",
  "eternal_beam_pet_binding",
  "eternal_beam_pet_name",
  "eternal_beam_theme_key",
  "eternal_beam_theme_id",
  "eternal_beam_selected_theme_id",
  "eternal_beam_background_theme_id",
  "eternal_beam_background_theme_name",
  "eternal_beam_custom_bg_video_url",
  "eternal_beam_custom_bg_job_id",
  "eternal_beam_custom_bg_content_id",
  "eternal_beam_canonical_scene_v1",
  "eternal_beam_nfc_payload",
  "eternal_beam_current_video_id",
  "eternal_beam_hologram_video_id",
];

/**
 * 펫별이지만 **보관하지 않는** 칸 — 활성 펫의 파생 뷰다.
 *
 * 전부 원본 미디어를 그대로 이고 있다(수 MB 의 data: URL). 세 마리분을 세션에
 * 쌓으면 용량을 넘기고, 넘칠 때 잃는 것은 펫을 가르는 가벼운 값들이다.
 * 전환할 때 비우고, 화면 상태에서 다시 적는다.
 */
const PER_PET_LIVE_ONLY = [
  "eternal_beam_main_photo",
  "eternal_beam_main_video_url",
  "eternal_beam_media_type",
];

/** 자리 기능 자체 — 키 안에서 이미 펫별로 나뉘어 있다. */
const SLOT_MACHINERY = [
  "eternal_beam_pet_slot_archive_v1",
  "eternal_beam_active_pet_slot_v1",
  "eternal_beam_phase1_intake_v1",
  // Phase 7 새로고침 재개 마커 — 단일 키, 값이 content_id 로 이미 나뉜 맵이다
  // (generation-resume.ts). content_id 자체가 자리를 가리키므로 pet-slot
  // 전환 때 갈아 끼울 필요가 없다 — phase1_intake_v1 과 같은 모양.
  "eternal_beam_active_generation_v1",
];

/**
 * 펫마다 답이 다른데 **아직 자리별로 나뉘지 않은** 칸들 — 지금은 비어 있다.
 *
 * 비어 있는 상태가 목표다. 여기에 무언가 들어간다는 것은 알면서 남겨 둔 누수가
 * 있다는 뜻이고, 그 결정은 눈에 보여야 한다.
 */
const PER_PET_UNSCOPED: string[] = [];

/** 사용자·기기·세션에 하나뿐 — 펫을 바꿔도 답이 같다. */
const APP_WIDE = [
  "eternal_beam_anon_id",
  "eternal_beam_user_id",
  "eternal_beam_lang",
  "eternal_beam_device_connected",
  "eternal_beam_credit_v1",
  "eternal_beam_place_id",
  "eternal_beam_shipping_address",
  "eternal_beam_billing_return_v1",
  "eternal_beam_theme_purchase_return_v1",
  "eternal_beam_soul_trace_handoff_v1",
  "eternal_beam_soul_trace_handoff_v2",
  "eternal_beam_soul_trace_pending_upload",
  "eternal_beam_soul_trace_active_letter_v1",
  "eternal_beam_server_cutout_disabled",
  // Phase 8 — My Beam 최근 전송 내역. 기기 하나에 한 칸이다(펫별로 나뉘지
  // 않는다 — 어느 펫의 명령이든 같은 브라우저가 보낸 최근 전송이다).
  "eternal_beam_recent_commands",
  // Phase 9 — My Library 새로고침 재개 표식. billing_return/theme_purchase_return
  // 과 같은 모양이다: 값 안에 petId 를 담고 있지만 칸 자체는 탭 하나에 한 개뿐이고,
  // pet-slot 전환 때 갈아 끼울 대상이 아니다(라이브러리는 인테이크 슬롯과 별개
  // 네임스페이스다 — 표식이 가리키는 펫이 지금도 있는지는 매번 서버 레지스트리로
  // 확인한다, library-flow-state.ts).
  "eternal_beam_library_flow_v1",
  // 비밀번호 재설정 링크 진입 표식 — 탭 하나·해시 하나에 한 번뿐이고, 어느
  // 펫이 활성인지와 무관하다(password-recovery.ts).
  "eternal_beam_password_recovery_v1",
];

function sourceFiles(): string[] {
  const files: string[] = [];
  for (const dir of [LIB_DIR, MEMORIAL_DIR]) {
    for (const name of readdirSync(dir)) {
      if (name.endsWith(".test.ts") || name.endsWith(".test.tsx")) continue;
      if (!name.endsWith(".ts") && !name.endsWith(".tsx")) continue;
      files.push(readFileSync(new URL(name, dir), "utf8"));
    }
  }
  files.push(readFileSync(new URL("../app/EternalBeamApp.tsx", import.meta.url), "utf8"));
  return files;
}

function discoveredKeys(): string[] {
  const found = new Set<string>();
  for (const source of sourceFiles()) {
    for (const match of source.matchAll(/["'](eternal_beam_[a-z0-9_]+)["']/g)) {
      found.add(match[1]);
    }
  }
  return [...found].sort();
}

test("every storage key in the intake surface is classified", () => {
  const classified = new Set([
    ...PER_PET_SCOPED,
    ...SLOT_MACHINERY,
    ...PER_PET_UNSCOPED,
    ...APP_WIDE,
  ]);
  const unclassified = discoveredKeys().filter((key) => !classified.has(key));
  assert.deepEqual(
    unclassified,
    [],
    `분류되지 않은 저장소 키가 있다. 펫마다 답이 달라지는 값이면 ` +
      `pet-slot-state.ts 의 ACTIVE_PET_KEYS 에 등록하고 PER_PET_SCOPED 에 적어라. ` +
      `아니라면 APP_WIDE 에 적어라: ${unclassified.join(", ")}`,
  );
});

test("every per-pet key is actually registered in ACTIVE_PET_KEYS", () => {
  const registered = new Set(ACTIVE_PET_STORAGE_KEYS);
  const missing = PER_PET_SCOPED.filter((key) => !registered.has(key));
  assert.deepEqual(missing, [], `등록부에 빠진 펫별 키: ${missing.join(", ")}`);
});

test("ACTIVE_PET_KEYS holds nothing beyond the declared per-pet set", () => {
  const declared = new Set(PER_PET_SCOPED);
  const stray = [...ACTIVE_PET_STORAGE_KEYS].filter((key) => !declared.has(key));
  assert.deepEqual(stray, [], `등록부에만 있고 분류에 없는 키: ${stray.join(", ")}`);
});

test("a key is never in two classifications at once", () => {
  const seen = new Map<string, string>();
  const groups: Array<[string, string[]]> = [
    ["PER_PET_SCOPED", PER_PET_SCOPED],
    ["SLOT_MACHINERY", SLOT_MACHINERY],
    ["PER_PET_UNSCOPED", PER_PET_UNSCOPED],
    ["APP_WIDE", APP_WIDE],
  ];
  for (const [name, keys] of groups) {
    for (const key of keys) {
      const previous = seen.get(key);
      assert.equal(previous, undefined, `${key} 가 ${previous} 와 ${name} 에 모두 있다`);
      seen.set(key, name);
    }
  }
});

test("an unscoped per-pet key is never quietly registered as scoped", () => {
  const registered = new Set(ACTIVE_PET_STORAGE_KEYS);
  const leaked = PER_PET_UNSCOPED.filter((key) => registered.has(key));
  assert.deepEqual(
    leaked,
    [],
    `등록했다면 PER_PET_UNSCOPED 에서 PER_PET_SCOPED 로 옮겨라: ${leaked.join(", ")}`,
  );
});

test("heavy media keys are registered but never archived for three pets", () => {
  const archived = new Set(ARCHIVED_PET_STORAGE_KEYS);
  const registered = new Set(ACTIVE_PET_STORAGE_KEYS);
  for (const key of PER_PET_LIVE_ONLY) {
    assert.ok(registered.has(key), `${key} 는 펫별 칸으로 등록돼 있어야 한다`);
    assert.ok(!archived.has(key), `${key} 를 보관하면 원본 사진이 세 마리분 쌓인다`);
  }
  // 보관 대상은 등록부에서 파생 뷰만 뺀 나머지다.
  assert.deepEqual(
    [...ARCHIVED_PET_STORAGE_KEYS].sort(),
    PER_PET_SCOPED.filter((key) => !PER_PET_LIVE_ONLY.includes(key)).sort(),
  );
});

test("theme, NFC, scene, video and binding keys are all scoped per pet", () => {
  const registered = new Set(ACTIVE_PET_STORAGE_KEYS);
  for (const key of [
    "eternal_beam_theme_key",
    "eternal_beam_theme_id",
    "eternal_beam_background_theme_id",
    "eternal_beam_selected_theme_id",
    "eternal_beam_custom_bg_video_url",
    "eternal_beam_nfc_payload",
    "eternal_beam_canonical_scene_v1",
    "eternal_beam_current_video_id",
    "eternal_beam_hologram_video_id",
    "eternal_beam_pet_id",
    "eternal_beam_pet_binding",
    "eternal_beam_pet_name",
  ]) {
    assert.ok(registered.has(key), `${key} 가 등록되지 않았다 — 펫 사이로 샌다`);
  }
});

test("no per-pet key is knowingly left unscoped", () => {
  assert.deepEqual(PER_PET_UNSCOPED, []);
});

test("the scan actually sees the keys it is guarding", () => {
  // 스캔이 조용히 0건을 반환하면 위의 모든 단언이 공허하게 통과한다.
  const found = new Set(discoveredKeys());
  for (const key of PER_PET_SCOPED) {
    assert.ok(found.has(key), `스캔이 ${key} 를 찾지 못했다 — 훑는 범위가 좁아졌다`);
  }
  assert.ok(found.size >= 30, `찾은 키가 ${found.size} 개뿐 — 스캔 범위를 확인하라`);
});
