import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  fetchLibraryPetIds,
  fetchMyLibraryPets,
  fetchPublishedMotionsForPet,
} from "./my-library-api.ts";

function response(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

test("fetchMyLibraryPets loads pets and published BREATHING URLs from backend", async () => {
  const urls: string[] = [];
  const result = await fetchMyLibraryPets({
    apiBase: "https://api.test",
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) => {
      urls.push(url);
      if (url.endsWith("/registry/mine")) {
        return response({ pets: [{ pet_id: "pet_1", content_id: "content-1" }] });
      }
      return response({
        pet_id: "pet_1",
        motion_id: "BREATHING",
        motion_version_id: "version-1",
        url: "https://signed.test/breathing.mp4",
        delivery_format: "packed_alpha",
        background_baked: true,
      });
    },
  });

  assert.deepEqual(result, [
    {
      petId: "pet_1",
      contentId: "content-1",
      motions: [
        {
          motionId: "BREATHING",
          version: 1,
          url: "https://signed.test/breathing.mp4",
          deliveryFormat: "packed_alpha",
          backgroundBaked: true,
        },
      ],
    },
  ]);
  assert.deepEqual(urls, [
    "https://api.test/api/v1/pet/registry/mine",
    "https://api.test/api/v1/pet/motions/pet_1/BREATHING/published",
  ]);
});

// ── delivery_format 누락 방어 ────────────────────────────────────────────────
//
// 실제 버그: 이 delivery_format 컬럼이 생기기 전에 발행된 낡은 레코드는
// 서버가 null 을 돌려준다. 그런데 이 백엔드 파이프라인은 BREATHING 발행에
// packed_alpha 밖에 만들지 않는다 — null 은 "다른 포맷"이 아니라 "아직 안
// 채워짐"이다. 그 null 을 그대로 흘려보내면 재생기가 휴리스틱(파일명/크로마)
// 으로 떨어지고, 그 추정이 어긋나면(서명 URL 이 원본 파일명을 보존하지 않거나
// 캔버스 픽셀 판독이 CORS 로 막히면) raw vstack(RGB+알파 매트)이 합성 없이
// 그대로 보인다.

test("delivery_format 이 null 이면(낡은 레코드) packed_alpha 로 채운다 — 백엔드가 그 밖의 포맷을 만들지 않는다", async () => {
  const result = await fetchMyLibraryPets({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) =>
      url.endsWith("/registry/mine")
        ? response({ pets: [{ pet_id: "pet_1", content_id: "content-1" }] })
        : response({
            pet_id: "pet_1",
            motion_id: "BREATHING",
            url: "https://signed.test/breathing.mp4",
            delivery_format: null,
            background_baked: false,
          }),
  });
  assert.equal(result[0]?.motions[0]?.deliveryFormat, "packed_alpha");
});

test("delivery_format 이 이미 명시돼 있으면 그 값을 그대로 쓴다 — 덮어쓰지 않는다", async () => {
  const result = await fetchMyLibraryPets({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) =>
      url.endsWith("/registry/mine")
        ? response({ pets: [{ pet_id: "pet_1", content_id: "content-1" }] })
        : response({
            pet_id: "pet_1",
            motion_id: "BREATHING",
            url: "https://signed.test/breathing.mp4",
            delivery_format: "packed_alpha",
            background_baked: false,
          }),
  });
  assert.equal(result[0]?.motions[0]?.deliveryFormat, "packed_alpha");
});

test("background_baked 인 구운 장면은 delivery_format 을 지어내지 않는다 — 다른 자산 모양이다", async () => {
  const result = await fetchMyLibraryPets({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) =>
      url.endsWith("/registry/mine")
        ? response({ pets: [{ pet_id: "pet_1", content_id: "content-1" }] })
        : response({
            pet_id: "pet_1",
            motion_id: "BREATHING",
            url: "https://signed.test/breathing.mp4",
            delivery_format: null,
            background_baked: true,
          }),
  });
  assert.equal(result[0]?.motions[0]?.deliveryFormat, null);
  assert.equal(result[0]?.motions[0]?.backgroundBaked, true);
});

test("unpublished or missing BREATHING pets are not shown in the library", async () => {
  const result = await fetchMyLibraryPets({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async (url) =>
      url.endsWith("/registry/mine")
        ? response({ pets: [{ pet_id: "pet_pending" }] })
        : response({ detail: { message: "not published" } }, 404),
  });
  assert.deepEqual(result, []);
});

// ── Phase 7: 펫별로 독립적으로 조회한다 — 한 펫의 실패가 다른 펫을 막지 않는다 ──

test("fetchLibraryPetIds: 발행 상태와 무관하게 등록된 펫 id 목록만 돌려준다", async () => {
  const result = await fetchLibraryPetIds({
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async () =>
      ({
        ok: true,
        status: 200,
        json: async () => ({
          pets: [
            { pet_id: "pet_1", content_id: "content-1" },
            { pet_id: "pet_2" },
            { pet_id: "" },
          ],
        }),
      }) as Response,
  });
  assert.deepEqual(result, [
    { petId: "pet_1", contentId: "content-1" },
    { petId: "pet_2", contentId: null },
  ]);
});

test("fetchPublishedMotionsForPet: 404 는 빈 목록 — 아직 발행되지 않은 펫도 에러가 아니다", async () => {
  const result = await fetchPublishedMotionsForPet("pet_pending", {
    getToken: async () => ({ token: "jwt", source: "supabase" }),
    fetchFn: async () => ({ ok: false, status: 404, json: async () => ({}) }) as Response,
  });
  assert.deepEqual(result, { motions: [], contentId: null });
});

test("fetchPublishedMotionsForPet: 그 밖의 실패 상태는 던진다 — 화면이 재시도 버튼을 달 수 있게", async () => {
  await assert.rejects(
    () =>
      fetchPublishedMotionsForPet("pet_1", {
        getToken: async () => ({ token: "jwt", source: "supabase" }),
        fetchFn: async () => ({ ok: false, status: 500, json: async () => ({}) }) as Response,
      }),
    /Published motion request failed \(500\)/
  );
});

test("한 펫의 조회 실패가 다른 펫의 조회에 영향을 주지 않는다 (부분 로딩 회복력)", async () => {
  const petMotionFetch = (petId: string) =>
    fetchPublishedMotionsForPet(petId, {
      getToken: async () => ({ token: "jwt", source: "supabase" }),
      fetchFn: async () => {
        if (petId === "pet_broken") {
          return { ok: false, status: 500, json: async () => ({}) } as Response;
        }
        return {
          ok: true,
          status: 200,
          json: async () => ({
            motion_id: "BREATHING",
            url: "https://signed.test/breathing.mp4",
            delivery_format: "packed_alpha",
            background_baked: false,
          }),
        } as Response;
      },
    });

  const results = await Promise.allSettled([
    petMotionFetch("pet_ok"),
    petMotionFetch("pet_broken"),
  ]);

  assert.equal(results[0].status, "fulfilled");
  assert.equal((results[0] as PromiseFulfilledResult<unknown>).value !== undefined, true);
  assert.equal(results[1].status, "rejected");
  // pet_ok 의 성공은 pet_broken 의 실패와 별개다 — Promise.all 처럼 하나가
  // 던지면 나머지도 함께 버려지는 조합을 화면이 쓰지 않는다는 것을 확정한다.
  const ok = results[0] as PromiseFulfilledResult<{ motions: unknown[] }>;
  assert.equal(ok.value.motions.length, 1);
});

test("library preview sends theme before pet asset and never supplies video_url", () => {
  const source = readFileSync("src/components/memorial/my-library-screen.tsx", "utf8");
  const themeIndex = source.indexOf('event: "theme_play"');
  const petIndex = source.indexOf('event: "pet_asset"');
  assert.ok(themeIndex >= 0 && themeIndex < petIndex);
  const petCommandBlock = source.slice(petIndex, source.indexOf("onComplete();", petIndex));
  assert.equal(petCommandBlock.includes("video_url"), false);
});
