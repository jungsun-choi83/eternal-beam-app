/**
 * 소유자 QR 카드 상태 모델.
 *
 * 조건이 다섯 개(자산·인증·로딩·목록·오류)라 컴포넌트 안에서 조합하면 반드시
 * 어긋난다. 특히 "링크는 있는데 원문을 몰라 보여 줄 수 없다"는 상태는 잊기 쉽고,
 * 잊으면 사용자에게 빈 칸을 보여 주게 된다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import type { ShareSummary } from "./shaker-share.ts";
import {
  deriveSharePanel,
  pickSharePoster,
  type SharePanelInput,
} from "./shaker-share-panel.ts";

function share(overrides: Partial<ShareSummary> = {}): ShareSummary {
  return {
    shareId: "shr_a",
    petId: "pet_goya",
    petName: "고야",
    createdAt: null,
    revokedAt: null,
    expiresAt: null,
    active: true,
    ...overrides,
  };
}

function input(overrides: Partial<SharePanelInput> = {}): SharePanelInput {
  return {
    hasBreathingAsset: true,
    hasAuth: true,
    loading: false,
    shares: [],
    error: null,
    justCreatedUrl: null,
    ...overrides,
  };
}

describe("게이트 순서", () => {
  it("BREATHING 이 없으면 로그인 여부와 무관하게 no-asset", () => {
    // 자산이 먼저다 — 공유할 대상이 없는데 로그인을 요구하면 순서가 거꾸로다.
    assert.equal(
      deriveSharePanel(input({ hasBreathingAsset: false, hasAuth: false })).phase,
      "no-asset"
    );
    assert.equal(
      deriveSharePanel(input({ hasBreathingAsset: false, hasAuth: true })).phase,
      "no-asset"
    );
  });

  it("자산이 있고 로그인이 없으면 signed-out", () => {
    assert.equal(deriveSharePanel(input({ hasAuth: false })).phase, "signed-out");
  });

  it("로딩 중이면 loading", () => {
    assert.equal(deriveSharePanel(input({ loading: true })).phase, "loading");
  });

  it("오류가 있으면 error", () => {
    assert.equal(deriveSharePanel(input({ error: "load" })).phase, "error");
  });
});

describe("링크 없음 / 있음", () => {
  it("활성 링크가 없으면 empty 이고 만들 수 있다", () => {
    const s = deriveSharePanel(input({ shares: [] }));
    assert.equal(s.phase, "empty");
    assert.equal(s.canCreate, true);
    assert.equal(s.canRevoke, false);
    assert.equal(s.activeCount, 0);
  });

  it("폐기된 링크만 있으면 여전히 empty 다", () => {
    const s = deriveSharePanel(input({ shares: [share({ active: false })] }));
    assert.equal(s.phase, "empty");
    assert.equal(s.activeCount, 0);
    assert.deepEqual(s.activeShareIds, []);
  });

  it("활성 링크가 있으면 active 이고 폐기할 수 있다", () => {
    const s = deriveSharePanel(input({ shares: [share()] }));
    assert.equal(s.phase, "active");
    assert.equal(s.canRevoke, true);
    assert.equal(s.activeCount, 1);
    assert.deepEqual(s.activeShareIds, ["shr_a"]);
  });

  it("활성 상태에서도 추가 발급이 가능하다 — 편지용·박스용을 따로 만든다", () => {
    assert.equal(deriveSharePanel(input({ shares: [share()] })).canCreate, true);
  });

  it("활성 링크만 센다", () => {
    const s = deriveSharePanel(
      input({
        shares: [
          share({ shareId: "a", active: true }),
          share({ shareId: "b", active: false }),
          share({ shareId: "c", active: true }),
        ],
      })
    );
    assert.equal(s.activeCount, 2);
    assert.deepEqual(s.activeShareIds, ["a", "c"]);
  });
});

describe("원문 토큰을 다시 볼 수 없다는 사실", () => {
  it("방금 만든 링크는 보여 준다", () => {
    const s = deriveSharePanel(
      input({ shares: [share()], justCreatedUrl: "https://x/shaker?share=tok" })
    );
    assert.equal(s.shareUrl, "https://x/shaker?share=tok");
    assert.equal(s.hasUnviewableLink, false);
  });

  it("링크는 있는데 방금 만든 것이 아니면 설명이 필요하다", () => {
    // 서버가 해시만 저장하므로 목록으로는 원문을 되살릴 수 없다.
    const s = deriveSharePanel(input({ shares: [share()] }));
    assert.equal(s.shareUrl, null);
    assert.equal(s.hasUnviewableLink, true);
  });

  it("링크가 없으면 설명할 것도 없다", () => {
    assert.equal(deriveSharePanel(input({ shares: [] })).hasUnviewableLink, false);
  });
});

describe("포스터 선택", () => {
  it("첫 번째 원격 URL 을 고른다", () => {
    assert.equal(
      pickSharePoster([null, "https://cdn/a.png", "https://cdn/b.png"]),
      "https://cdn/a.png"
    );
  });

  it("data: URL 은 건너뛴다 — 서버가 400 으로 거절한다", () => {
    assert.equal(
      pickSharePoster(["data:image/png;base64,AAA", "https://cdn/a.png"]),
      "https://cdn/a.png"
    );
  });

  it("blob: 과 빈 값도 건너뛴다", () => {
    assert.equal(pickSharePoster(["", "  ", "blob:https://x/y", "https://cdn/a.png"]), "https://cdn/a.png");
  });

  it("쓸 수 있는 것이 없으면 null — 포스터는 선택이다", () => {
    assert.equal(pickSharePoster([null, undefined, "data:image/png;base64,AAA"]), null);
    assert.equal(pickSharePoster([]), null);
  });
});
