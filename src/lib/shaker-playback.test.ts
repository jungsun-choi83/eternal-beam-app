/**
 * Shaker 재생 배선 — 서버 정책과 런타임 등록을 **둘 다** 통과한 것만 재생된다.
 *
 * 이 파일이 고정하는 회귀: 서버가 허락했다는 이유만으로 UI 가 "더블탭해 보세요"를
 * 띄우고 실제로는 아무 일도 일어나지 않는 상태.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import type { ShakerPet } from "./shaker-api.ts";
import {
  buildShakerViewModel,
  resolveShakerDoubleTap,
  shakerEventSources,
  shouldShowDoubleTapHint,
} from "./shaker-playback.ts";

function pet(overrides: Partial<ShakerPet> = {}): ShakerPet {
  return {
    petId: "pet_goya",
    petName: "고야",
    breathingUrl: "https://cdn.test/idle.mp4",
    posterUrl: null,
    actions: [],
    doubleTapActionId: null,
    ...overrides,
  };
}

const CC = { id: "COME_CLOSER", url: "https://cdn.test/cc.mp4" };
const BLINK = { id: "BLINKING", url: "https://cdn.test/blink.mp4" };
/** 이 빌드의 런타임이 모르는 id — 서버가 앞서 배포된 상황을 흉내 낸다. */
const UNKNOWN = { id: "SOMERSAULT", url: "https://cdn.test/flip.mp4" };

describe("이벤트 소스 변환", () => {
  it("더블탭이 재생할 액션 하나만 소스가 된다", () => {
    const sources = shakerEventSources(
      pet({ actions: [CC], doubleTapActionId: "COME_CLOSER" })
    );
    assert.deepEqual(sources, { COME_CLOSER: CC.url });
  });

  it("허락된 액션이 여러 개여도 하나만 마운트한다", () => {
    // Shaker 에는 자발적 스케줄러가 없다. 나머지는 영원히 재생되지 않으므로
    // <video> 를 만들어 모바일 대역폭·디코더를 쓰게 두지 않는다.
    const sources = shakerEventSources(
      pet({ actions: [CC, BLINK], doubleTapActionId: "COME_CLOSER" })
    );
    assert.deepEqual(sources, { COME_CLOSER: CC.url });
  });

  it("런타임이 모르는 id 는 소스가 되지 않는다 (버전 스큐)", () => {
    const sources = shakerEventSources(
      pet({ actions: [UNKNOWN], doubleTapActionId: "SOMERSAULT" })
    );
    assert.deepEqual(sources, {});
  });

  it("액션이 없으면 빈 표 — 더블탭이 구조적으로 아무 일도 하지 않는다", () => {
    assert.deepEqual(shakerEventSources(pet()), {});
    assert.deepEqual(shakerEventSources(null), {});
  });

  it("URL 이 빈 액션은 소스가 되지 않는다", () => {
    const sources = shakerEventSources(
      pet({ actions: [{ id: "COME_CLOSER", url: "" }], doubleTapActionId: "COME_CLOSER" })
    );
    assert.deepEqual(sources, {});
  });
});

describe("더블탭 판정", () => {
  it("서버가 지목했고 등록돼 있으면 재생 가능", () => {
    const d = resolveShakerDoubleTap(pet({ actions: [CC], doubleTapActionId: "COME_CLOSER" }));
    assert.deepEqual(d, { available: true, actionId: "COME_CLOSER" });
  });

  it("기본 정책(PM 미결)에서는 재생 불가", () => {
    // 서버가 액션을 하나도 허락하지 않은 상태 — 이것이 현재 기본값이다.
    const d = resolveShakerDoubleTap(pet());
    assert.deepEqual(d, { available: false, reason: "no-permitted-action" });
  });

  it("아직 안 불러왔으면 not-loaded 로 구분한다", () => {
    assert.deepEqual(resolveShakerDoubleTap(null), {
      available: false,
      reason: "not-loaded",
    });
  });

  it("서버가 등록되지 않은 id 를 지목하면 거절한다 (버전 스큐)", () => {
    const d = resolveShakerDoubleTap(
      pet({ actions: [UNKNOWN], doubleTapActionId: "SOMERSAULT" })
    );
    assert.deepEqual(d, { available: false, reason: "not-registered" });
  });

  it("등록된 아이들 행동을 지목해도 재생 가능하다", () => {
    // 다섯 행동이 모두 RUNTIME_EVENTS 에 있다. 정책이 COME_CLOSER 가 아닌 것을
    // 고를 수 있으므로(A 정책) 이 경로도 살아 있어야 한다.
    const d = resolveShakerDoubleTap(
      pet({ actions: [BLINK], doubleTapActionId: "BLINKING" })
    );
    assert.deepEqual(d, { available: true, actionId: "BLINKING" });
  });

  it("지목된 액션에 소스가 없으면 거절한다", () => {
    const d = resolveShakerDoubleTap(
      pet({ actions: [BLINK], doubleTapActionId: "COME_CLOSER" })
    );
    assert.equal(d.available, false);
  });

  it("지목이 재생 불가일 때 다른 액션으로 대체하지 않는다", () => {
    // 정책이 고른 것과 다른 것을 재생하는 편이 아무것도 안 하는 것보다 나쁘다.
    const d = resolveShakerDoubleTap(
      pet({ actions: [CC, UNKNOWN], doubleTapActionId: "SOMERSAULT" })
    );
    assert.equal(d.available, false);
  });
});

describe("더블탭 힌트", () => {
  it("재생 가능할 때만 보여 준다", () => {
    assert.equal(
      shouldShowDoubleTapHint(pet({ actions: [CC], doubleTapActionId: "COME_CLOSER" })),
      true
    );
  });

  it("정책이 꺼져 있으면 안내하지 않는다 — 먹통 안내는 버그처럼 보인다", () => {
    assert.equal(shouldShowDoubleTapHint(pet()), false);
    assert.equal(shouldShowDoubleTapHint(null), false);
  });
});

describe("화면 모델", () => {
  it("BREATHING 은 액션 유무와 무관하게 언제나 실린다", () => {
    // 무료라는 규칙이 화면 모델 수준에서도 성립하는지 본다.
    const vm = buildShakerViewModel(pet());
    assert.equal(vm.breathingUrl, "https://cdn.test/idle.mp4");
    assert.deepEqual(vm.eventSources, {});
    assert.equal(vm.doubleTap.available, false);
  });

  it("액션이 있으면 소스와 더블탭이 함께 채워진다", () => {
    const vm = buildShakerViewModel(
      pet({ actions: [CC], doubleTapActionId: "COME_CLOSER", posterUrl: "https://p/x.png" })
    );
    assert.deepEqual(vm.eventSources, { COME_CLOSER: CC.url });
    assert.deepEqual(vm.doubleTap, { available: true, actionId: "COME_CLOSER" });
    assert.equal(vm.posterUrl, "https://p/x.png");
  });
});
