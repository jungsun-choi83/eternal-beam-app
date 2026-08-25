/**
 * 공개 Shaker API 클라이언트 — 파싱과 오류 분류.
 *
 * 네트워크를 타지 않는 순수 함수만 검증한다. fetch 배선은 화면 통합 지점이라
 * 여기서 흉내 내면 실제와 다른 것을 검증하게 된다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  classifyShakerError,
  parseShakerPet,
  resolveAssetUrl,
  ShakerApiError,
} from "./shaker-api.ts";

const FULL = {
  pet_id: "pet_goya",
  pet_name: "고야",
  breathing_url: "https://cdn.test/idle.mp4",
  poster_url: "https://cdn.test/poster.png",
  actions: [{ id: "COME_CLOSER", url: "https://cdn.test/cc.mp4" }],
  double_tap_action_id: "COME_CLOSER",
};

describe("응답 파싱", () => {
  it("전체 응답을 camelCase 로 옮긴다", () => {
    const p = parseShakerPet(FULL);
    assert.equal(p.petId, "pet_goya");
    assert.equal(p.petName, "고야");
    assert.equal(p.breathingUrl, "https://cdn.test/idle.mp4");
    assert.equal(p.posterUrl, "https://cdn.test/poster.png");
    assert.deepEqual(p.actions, [{ id: "COME_CLOSER", url: "https://cdn.test/cc.mp4" }]);
    assert.equal(p.doubleTapActionId, "COME_CLOSER");
  });

  it("기본 정책(액션 없음)을 그대로 받는다", () => {
    // PM 미결 상태의 서버 응답이 이 모양이다. 프론트가 임의로 채워 넣지 않는다.
    const p = parseShakerPet({ ...FULL, actions: [], double_tap_action_id: null });
    assert.deepEqual(p.actions, []);
    assert.equal(p.doubleTapActionId, null);
  });

  it("선택 필드가 없어도 깨지지 않는다", () => {
    const p = parseShakerPet({ breathing_url: "https://cdn.test/idle.mp4" });
    assert.equal(p.petName, null);
    assert.equal(p.posterUrl, null);
    assert.deepEqual(p.actions, []);
    assert.equal(p.doubleTapActionId, null);
  });

  it("빈 문자열 이름/포스터는 null 로 정규화한다", () => {
    const p = parseShakerPet({ ...FULL, pet_name: "   ", poster_url: "" });
    assert.equal(p.petName, null);
    assert.equal(p.posterUrl, null);
  });

  it("id 나 url 이 빈 액션은 버린다 — 눌러도 재생되지 않을 버튼을 만들지 않는다", () => {
    const p = parseShakerPet({
      ...FULL,
      actions: [
        { id: "COME_CLOSER", url: "https://cdn.test/cc.mp4" },
        { id: "", url: "https://cdn.test/x.mp4" },
        { id: "BLINKING", url: "" },
        { id: "TAIL_WAGGING" },
      ],
    });
    assert.deepEqual(p.actions.map((a) => a.id), ["COME_CLOSER"]);
  });

  it("액션 id 를 대문자로 정규화한다", () => {
    const p = parseShakerPet({
      ...FULL,
      actions: [{ id: "come_closer", url: "https://cdn.test/cc.mp4" }],
      double_tap_action_id: "come_closer",
    });
    assert.equal(p.actions[0].id, "COME_CLOSER");
    assert.equal(p.doubleTapActionId, "COME_CLOSER");
  });

  it("actions 에 없는 double_tap_action_id 는 무시한다", () => {
    // 서버 버그의 증상이 "탭했는데 아무 일도 없음"이 되지 않도록 여기서 끊는다.
    const p = parseShakerPet({ ...FULL, actions: [], double_tap_action_id: "COME_CLOSER" });
    assert.equal(p.doubleTapActionId, null);
  });

  it("actions 가 배열이 아니어도 죽지 않는다", () => {
    const p = parseShakerPet({ ...FULL, actions: "nope" as unknown as [] });
    assert.deepEqual(p.actions, []);
  });
});

describe("오류 분류", () => {
  it("본문의 code 를 상태 코드보다 우선한다", () => {
    assert.equal(
      classifyShakerError(410, { detail: { code: "SHARE_EXPIRED" } }),
      "SHARE_EXPIRED"
    );
    assert.equal(
      classifyShakerError(410, { detail: { code: "SHARE_REVOKED" } }),
      "SHARE_REVOKED"
    );
  });

  it("본문이 없으면 상태 코드로 추론한다", () => {
    assert.equal(classifyShakerError(404, null), "SHARE_NOT_FOUND");
    assert.equal(classifyShakerError(410, null), "SHARE_REVOKED");
    assert.equal(classifyShakerError(429, null), "RATE_LIMITED");
    assert.equal(classifyShakerError(422, null), "SHARE_TOKEN_REQUIRED");
    assert.equal(classifyShakerError(503, null), "SHARE_STORE_UNAVAILABLE");
    assert.equal(classifyShakerError(418, null), "UNKNOWN");
  });

  it("모르는 code 는 상태 코드 추론으로 떨어진다", () => {
    assert.equal(
      classifyShakerError(404, { detail: { code: "SOMETHING_NEW" } }),
      "SHARE_NOT_FOUND"
    );
  });
});

describe("재시도 가능 여부", () => {
  it("일시적 실패만 재시도 대상이다", () => {
    for (const code of ["NETWORK", "RATE_LIMITED", "SHARE_STORE_UNAVAILABLE"] as const) {
      assert.equal(new ShakerApiError(code, "x", 0).retryable, true, code);
    }
  });

  it("링크 자체가 죽은 경우는 재시도해도 소용없다", () => {
    // [다시 시도] 버튼을 띄우면 사용자가 무의미하게 반복하게 된다.
    for (const code of ["SHARE_NOT_FOUND", "SHARE_REVOKED", "SHARE_EXPIRED"] as const) {
      assert.equal(new ShakerApiError(code, "x", 404).retryable, false, code);
    }
  });
});

describe("재생 URL 해석", () => {
  it("상대 프록시 경로에 API 베이스를 붙인다", () => {
    // 별도 API 도메인 배포에서는 상대 경로가 웹 도메인으로 해석돼 404 가 난다.
    assert.equal(
      resolveAssetUrl("/api/v1/shaker/asset?share=t&k=breathing", "https://api.eternalbeam.com"),
      "https://api.eternalbeam.com/api/v1/shaker/asset?share=t&k=breathing"
    );
  });

  it("같은 오리진(베이스 없음)이면 상대 경로 그대로", () => {
    assert.equal(
      resolveAssetUrl("/api/v1/shaker/asset?share=t&k=breathing", ""),
      "/api/v1/shaker/asset?share=t&k=breathing"
    );
  });

  it("절대 URL 은 건드리지 않는다 (프록시를 끈 설정)", () => {
    const abs = "https://proj.supabase.co/storage/v1/object/sign/b/o.mp4?token=x";
    assert.equal(resolveAssetUrl(abs, "https://api.eternalbeam.com"), abs);
  });

  it("빈 값은 빈 값이다", () => {
    assert.equal(resolveAssetUrl("", "https://api.x"), "");
  });

  it("파싱이 프록시 경로를 해석해 넣는다", () => {
    const p = parseShakerPet({
      breathing_url: "/api/v1/shaker/asset?share=t&k=breathing",
      poster_url: "/api/v1/shaker/asset?share=t&k=poster",
      actions: [{ id: "COME_CLOSER", url: "/api/v1/shaker/asset?share=t&k=COME_CLOSER" }],
      double_tap_action_id: "COME_CLOSER",
    });
    // node --test 에는 import.meta.env 가 없어 베이스가 빈 문자열이다 —
    // 상대 경로가 그대로 유지되는지만 본다.
    assert.equal(p.breathingUrl, "/api/v1/shaker/asset?share=t&k=breathing");
    assert.equal(p.actions[0].url, "/api/v1/shaker/asset?share=t&k=COME_CLOSER");
  });
});

describe("구운 자산이 QR 재생까지 도달한다 (Phase 27)", () => {

it("서버가 background_baked=true 를 주면 키잉하지 않는다", async () => {
  // 이 배선은 원래부터 맞았다 — 서버가 필드를 보낸 적이 없었을 뿐이다.
  // 이제 shaker_v1.ShakerPetOut 이 실어 보내므로 끝까지 이어진다.
  const { buildShakerViewModel } = await import("./shaker-playback.ts");
  const { shouldTransparentComposite } = await import("./baked-playback.ts");

  const pet = parseShakerPet({
    pet_id: "pet_c1",
    breathing_url: "https://cdn.test/idle.mp4",
    background_baked: true,
  });
  assert.equal(pet.backgroundBaked, true);

  const vm = buildShakerViewModel(pet);
  assert.equal(vm.backgroundBaked, true);
  assert.equal(
    shouldTransparentComposite({ backgroundBaked: vm.backgroundBaked }),
    false,
    "구운 영상에 블랙키 제거가 걸린다 — 그림자가 뚫린다"
  );
});

it("필드가 없는 응답(마이그레이션 전 인쇄물)은 레거시로 재생된다", async () => {
  // 기존 QR 이 전부 여기 해당한다. false 로 읽히지 않으면 한꺼번에 깨진다.
  const { buildShakerViewModel } = await import("./shaker-playback.ts");
  const { shouldTransparentComposite } = await import("./baked-playback.ts");

  const pet = parseShakerPet({
    pet_id: "pet_c1",
    breathing_url: "https://cdn.test/idle.mp4",
  });
  assert.equal(pet.backgroundBaked, false);
  const vm = buildShakerViewModel(pet);
  assert.equal(shouldTransparentComposite({ backgroundBaked: vm.backgroundBaked }), true);
});

it("true 가 아닌 값은 전부 레거시다 — 문자열도 참으로 치지 않는다", () => {
  for (const v of ["true", 1, "1", {}, null, undefined]) {
    const pet = parseShakerPet({
      pet_id: "p",
      breathing_url: "https://cdn.test/i.mp4",
      background_baked: v,
    });
    assert.equal(pet.backgroundBaked, false, String(v));
  }
});

it("서버 응답 모델과 프론트 파서가 같은 이름을 쓴다", async () => {
  // 이름이 어긋나면 조용히 false 가 된다 — 정확히 그 결함을 고치는 중이다.
  const { readFileSync } = await import("node:fs");
  const router = readFileSync("backend/routers/shaker_v1.py", "utf8");
  // 응답 모델의 실제 이름은 ShakerPetResponse 다.
  const i = router.indexOf("class ShakerPetResponse");
  assert.ok(i > 0, "응답 모델을 찾지 못했다");
  assert.match(
    router.slice(i, router.indexOf("\n\ndef ", i)),
    /background_baked: bool = False/,
    "응답 모델에 background_baked 필드가 없다"
  );
  assert.match(router, /background_baked=resolved\.rec\.background_baked/);
});
});
