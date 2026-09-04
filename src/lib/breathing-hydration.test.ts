/**
 * Phase 7F — 발행 BREATHING 하이드레이션.
 *
 * 순수 적용 함수와 fetch 래퍼를 검증한다. sessionStorage 오케스트레이션
 * (hydrateStoredPipeline)은 DOM 전용이라 여기서는 부품 단위로 본다 —
 * 배선(화면이 실제로 부르는가)은 packed-delivery-wiring.test.ts 가 본다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  applyPublishedBreathing,
  fetchPublishedBreathing,
  resolveHydrationPetId,
  type PublishedBreathingResponse,
} from "./breathing-hydration.ts";

const PUBLISHED: PublishedBreathingResponse = {
  pet_id: "pet_abc",
  motion_id: "BREATHING",
  breathing_bucket: "user-assets",
  breathing_object_path: "u/c/motions/breathing/v1/seedance_a1_packed.mp4",
  url: "https://storage.test/user-assets/u/c/motions/breathing/v1/seedance_a1_packed.mp4?token=fresh",
  background_baked: false,
  motion_version_id: "v-1",
  delivery_format: "packed_alpha",
  publication_id: "pub-1",
};

const PIPELINE = {
  content_id: "abc",
  cutout_display_url: "https://cdn/cutout.png",
  dog_only_nobg_url: "https://cdn/dog.png",
  idle_video_url: "https://cdn/stale.mp4?token=old",
  action_video_url: "https://cdn/action.mp4",
  come_closer_video_url: "https://cdn/come.mp4",
  background_baked: true,
  scene_id: "scene-9",
  phase1_intake: {
    status: "ready",
    pet_id: "pet_abc",
    original_reference_id: "r1",
    cutout_reference_id: "r2",
  },
};

test("applyPublishedBreathing: 서명 URL·포맷·baked 만 갱신하고 나머지는 보존", () => {
  const next = applyPublishedBreathing(PIPELINE, PUBLISHED);
  assert.equal(next.idle_video_url, PUBLISHED.url);
  assert.equal(next.background_baked, false);
  assert.equal(next.delivery_format, "packed_alpha");
  // 나머지 필드는 그대로다 — 하이드레이션은 BREATH 포인터만 만진다.
  assert.equal(next.cutout_display_url, PIPELINE.cutout_display_url);
  assert.equal(next.come_closer_video_url, PIPELINE.come_closer_video_url);
  assert.equal(next.scene_id, PIPELINE.scene_id);
  assert.deepEqual(next.phase1_intake, PIPELINE.phase1_intake);
  // 원본 객체는 불변이다.
  assert.equal(PIPELINE.idle_video_url, "https://cdn/stale.mp4?token=old");
});

test("applyPublishedBreathing: 레거시 발행(포맷 없음)은 delivery_format=null", () => {
  const next = applyPublishedBreathing(PIPELINE, {
    ...PUBLISHED,
    delivery_format: null,
    background_baked: true,
  });
  assert.equal(next.delivery_format, null);
  assert.equal(next.background_baked, true);
});

test("applyPublishedBreathing: 빈 URL 은 아무것도 덮지 않는다", () => {
  const next = applyPublishedBreathing(PIPELINE, { ...PUBLISHED, url: "  " });
  assert.equal(next, PIPELINE);
});

test("resolveHydrationPetId: Phase 7B 영수증이 1순위", () => {
  assert.equal(resolveHydrationPetId(PIPELINE), "pet_abc");
  // 영수증이 없고 window 도 없으면(node) null — 조용한 no-op 경로.
  assert.equal(resolveHydrationPetId({ content_id: "abc" }), null);
  assert.equal(resolveHydrationPetId(null), null);
});

function fakeToken(token: string | null) {
  return async () =>
    token
      ? ({ token, source: "supabase" } as const)
      : ({ token: null, source: "none", reason: "no-session" } as const);
}

test("fetchPublishedBreathing: 인증 헤더 + 정확한 경로로 호출하고 본문을 돌려준다", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetchFn = (async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return {
      ok: true,
      json: async () => PUBLISHED,
    } as Response;
  }) as unknown as typeof fetch;

  const result = await fetchPublishedBreathing("pet_abc", {
    fetchFn,
    getToken: fakeToken("jwt-123"),
    apiBase: "https://api.test",
  });
  assert.deepEqual(result, PUBLISHED);
  assert.equal(calls.length, 1);
  assert.equal(
    calls[0].url,
    "https://api.test/api/v1/pet/motions/pet_abc/BREATHING/published"
  );
  assert.equal(
    (calls[0].init?.headers as Record<string, string>).Authorization,
    "Bearer jwt-123"
  );
});

test("fetchPublishedBreathing: 404/미발행·토큰 없음·URL 없는 본문 → null (절대 던지지 않음)", async () => {
  const notFound = (async () => ({ ok: false, status: 404 })) as unknown as typeof fetch;
  assert.equal(
    await fetchPublishedBreathing("pet_abc", { fetchFn: notFound, getToken: fakeToken("t") }),
    null
  );

  let fetched = 0;
  const counting = (async () => {
    fetched += 1;
    return { ok: true, json: async () => PUBLISHED };
  }) as unknown as typeof fetch;
  assert.equal(
    await fetchPublishedBreathing("pet_abc", { fetchFn: counting, getToken: fakeToken(null) }),
    null
  );
  assert.equal(fetched, 0); // 세션 없이 네트워크를 건드리지 않는다

  const noUrl = (async () => ({
    ok: true,
    json: async () => ({ ...PUBLISHED, url: "" }),
  })) as unknown as typeof fetch;
  assert.equal(
    await fetchPublishedBreathing("pet_abc", { fetchFn: noUrl, getToken: fakeToken("t") }),
    null
  );

  const throwing = (async () => {
    throw new Error("network down");
  }) as unknown as typeof fetch;
  assert.equal(
    await fetchPublishedBreathing("pet_abc", { fetchFn: throwing, getToken: fakeToken("t") }),
    null
  );
});
