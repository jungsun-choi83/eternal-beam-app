/**
 * Phase 7I.1 — READY 자산 계약: ready_assets 파싱 + 이벤트별 렌더 모드.
 *
 * 발견(discovery)이 돌려주는 {url, delivery_format} 이 재생기 모드까지
 * 어떻게 이어지는지의 순수 함수 부분을 고정한다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { parseReadyAssets } from "./premium-assets.ts";
import { eventRenderMode } from "./baked-playback.ts";

// ── parseReadyAssets ─────────────────────────────────────────────────────────

test("새 서버: ready_assets 를 그대로 파싱한다 — 혼합 포맷 유지", () => {
  const out = parseReadyAssets(
    {
      BLINKING: { url: "https://s/b_packed.mp4?token=f", delivery_format: "packed_alpha" },
      COME_CLOSER: { url: "https://s/cc.mp4?token=f", delivery_format: null },
    },
    { BLINKING: "https://s/b_packed.mp4?token=f", COME_CLOSER: "https://s/cc.mp4?token=f" }
  );
  assert.equal(out.BLINKING.deliveryFormat, "packed_alpha");
  assert.equal(out.COME_CLOSER.deliveryFormat, null);
});

test("구서버(필드 없음): ready 에서 파생하고 포맷은 null(레거시 규칙)", () => {
  const out = parseReadyAssets(undefined, {
    TAIL_WAGGING: "https://s/t.mp4?token=old",
  });
  assert.deepEqual(out.TAIL_WAGGING, {
    url: "https://s/t.mp4?token=old",
    deliveryFormat: null,
  });
});

test("빈/깨진 항목은 버린다 — 빈 URL 이 후보가 되면 스케줄러가 죽은 소스를 고른다", () => {
  const out = parseReadyAssets(
    { A: { url: "  " }, B: { url: 42 }, C: "nope" },
    { D: "" }
  );
  assert.deepEqual(Object.keys(out), []);
});

test("ready_assets 가 우선한다 — ready 는 같은 키를 덮지 않는다", () => {
  const out = parseReadyAssets(
    { BLINKING: { url: "https://s/new.mp4", delivery_format: "packed_alpha" } },
    { BLINKING: "https://s/old.mp4" }
  );
  assert.equal(out.BLINKING.url, "https://s/new.mp4");
  assert.equal(out.BLINKING.deliveryFormat, "packed_alpha");
});

// ── eventRenderMode — 이벤트 모드는 BREATH 에서 파생되지 않는다 ─────────────

test("명시 포맷이 1순위: packed_alpha→packed, baked→raw, blackkey→blackkey", () => {
  assert.equal(eventRenderMode("packed_alpha", "https://s/x.mp4"), "packed");
  assert.equal(eventRenderMode("baked", "https://s/x.mp4"), "raw");
  assert.equal(eventRenderMode("blackkey", "https://s/x_packed.mp4"), "blackkey");
  assert.equal(eventRenderMode(" PACKED_ALPHA ", null), "packed");
});

test("선언이 없으면 파일명 규칙 → 레거시 기본(blackkey)", () => {
  assert.equal(eventRenderMode(null, "https://s/b_packed.mp4?token=f"), "packed");
  assert.equal(eventRenderMode(undefined, "https://s/legacy.mp4"), "blackkey");
  assert.equal(eventRenderMode("", null), "blackkey");
  // 알 수 없는 미래 포맷도 레거시로 떨어진다 — 반토막(가짜 packed)이 최악이다.
  assert.equal(eventRenderMode("future_format", "https://s/x.mp4"), "blackkey");
});
