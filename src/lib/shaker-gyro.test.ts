/**
 * 자이로 패럴랙스 — 은은함·기준점·접근성.
 *
 * 여기서 지키는 것은 시각적 취향이 아니라 **상한**이다. 누가 설정을 키워도
 * 게임처럼 되지 않아야 하고, 움직임 최소화를 켠 사용자에게는 완전히 꺼져야 한다.
 */

import { strict as assert } from "node:assert";
import { describe, it } from "node:test";

import {
  NO_PARALLAX,
  PARALLAX_DEFAULT,
  createParallaxTracker,
  normalizeTilt,
  pointerToGyroSample,
  sanitizeParallaxConfig,
  shouldAnimateParallax,
} from "./shaker-gyro.ts";

/** 감쇠를 통과시키기 위해 같은 샘플을 여러 번 넣는다 (지수 감쇠는 점근한다). */
function settle(
  tracker: ReturnType<typeof createParallaxTracker>,
  sample: { beta: number; gamma: number },
  times = 400
) {
  let last = NO_PARALLAX;
  for (let i = 0; i < times; i++) last = tracker.push(sample);
  return last;
}

describe("기울기 정규화", () => {
  it("데드존 안은 0 이다", () => {
    assert.equal(normalizeTilt(0, 26, 1.2), 0);
    assert.equal(normalizeTilt(1.0, 26, 1.2), 0);
    assert.equal(normalizeTilt(-1.0, 26, 1.2), 0);
  });

  it("데드존을 막 벗어나면 0 에서 시작한다 — 경계에서 튀지 않는다", () => {
    const justOutside = normalizeTilt(1.3, 26, 1.2);
    assert.ok(justOutside > 0 && justOutside < 0.02, `튐: ${justOutside}`);
  });

  it("범위 끝에서 1 이고, 넘어도 1 을 넘지 않는다", () => {
    assert.equal(normalizeTilt(26, 26, 1.2), 1);
    assert.equal(normalizeTilt(90, 26, 1.2), 1);
    assert.equal(normalizeTilt(-90, 26, 1.2), -1);
  });

  it("부호를 보존한다", () => {
    assert.ok(normalizeTilt(10, 26, 1.2) > 0);
    assert.ok(normalizeTilt(-10, 26, 1.2) < 0);
  });
});

describe("은은함 상한", () => {
  it("기본값이 작다 — 눈에 띄면 깊이가 아니라 효과로 보인다", () => {
    assert.ok(PARALLAX_DEFAULT.petMaxPx <= 12);
    assert.ok(PARALLAX_DEFAULT.backgroundMaxPx < PARALLAX_DEFAULT.petMaxPx);
  });

  it("설정을 크게 넣어도 상한에서 잘린다", () => {
    const c = sanitizeParallaxConfig({ petMaxPx: 500, backgroundMaxPx: 900 });
    assert.equal(c.petMaxPx, 16);
    assert.equal(c.backgroundMaxPx, 8);
  });

  it("음수·0 설정도 안전하게 정리된다", () => {
    const c = sanitizeParallaxConfig({ petMaxPx: -5, smoothing: 0, rangeDeg: 0 });
    assert.equal(c.petMaxPx, 0);
    assert.ok(c.smoothing > 0);
    assert.ok(c.rangeDeg >= 5);
  });

  it("최대로 기울여도 펫은 상한 픽셀을 넘지 않는다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    const f = settle(t, { beta: 90, gamma: 90 });
    assert.ok(Math.abs(f.pet.x) <= PARALLAX_DEFAULT.petMaxPx + 1e-6);
    assert.ok(Math.abs(f.pet.y) <= PARALLAX_DEFAULT.petMaxPx + 1e-6);
  });
});

describe("깊이감", () => {
  it("배경이 펫보다 덜 움직인다 — 멀리 있기 때문이다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    const f = settle(t, { beta: 20, gamma: 20 });
    assert.ok(Math.abs(f.background.x) < Math.abs(f.pet.x));
    assert.ok(Math.abs(f.background.y) < Math.abs(f.pet.y));
  });

  it("두 레이어가 같은 방향으로 움직인다 — 반대면 찢어져 보인다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    const f = settle(t, { beta: 20, gamma: 20 });
    assert.equal(Math.sign(f.pet.x), Math.sign(f.background.x));
    assert.equal(Math.sign(f.pet.y), Math.sign(f.background.y));
  });
});

describe("기준 자세", () => {
  it("첫 샘플이 0 이 된다 — 폰을 기울여 들고 봐도 여유가 남는다", () => {
    const t = createParallaxTracker();
    // 사람은 보통 45° 쯤 기울여 든다. 절대각을 쓰면 시작부터 최대치에 붙는다.
    const first = t.push({ beta: 45, gamma: 0 });
    assert.equal(first.pet.x, 0);
    assert.equal(first.pet.y, 0);
  });

  it("기준에서 벗어난 만큼만 움직인다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 45, gamma: 0 });
    const f = settle(t, { beta: 45, gamma: 0 });
    assert.ok(Math.abs(f.pet.y) < 1e-6, "기준 자세로 돌아오면 0 이어야 한다");
  });

  it("reset 하면 기준을 다시 잡는다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    settle(t, { beta: 20, gamma: 0 });
    t.reset();
    const after = t.push({ beta: 20, gamma: 0 });
    assert.equal(after.pet.y, 0);
  });
});

describe("센서 이상값", () => {
  it("null 샘플은 마지막 값을 유지한다 — 중앙으로 튀지 않는다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    const moved = settle(t, { beta: 20, gamma: 20 });
    const after = t.push({ beta: null, gamma: null });
    assert.deepEqual(after, moved);
  });

  it("NaN/Infinity 도 무시한다", () => {
    const t = createParallaxTracker();
    t.push({ beta: 0, gamma: 0 });
    const moved = settle(t, { beta: 15, gamma: 15 });
    assert.deepEqual(t.push({ beta: NaN, gamma: 5 }), moved);
    assert.deepEqual(t.push({ beta: Infinity, gamma: 5 }), moved);
  });

  it("가만히 있으면 떨지 않는다 (데드존)", () => {
    const t = createParallaxTracker();
    t.push({ beta: 30, gamma: 10 });
    // 손 떨림 수준의 미세 흔들림.
    for (let i = 0; i < 50; i++) {
      t.push({ beta: 30 + (i % 2 ? 0.4 : -0.4), gamma: 10 + (i % 2 ? -0.3 : 0.3) });
    }
    const f = t.current();
    assert.ok(Math.abs(f.pet.x) < 0.01, `x 떨림: ${f.pet.x}`);
    assert.ok(Math.abs(f.pet.y) < 0.01, `y 떨림: ${f.pet.y}`);
  });
});

describe("접근성 · 폴백 게이트", () => {
  it("움직임 최소화가 켜지면 완전히 끈다 — 줄이는 것이 아니다", () => {
    assert.equal(
      shouldAnimateParallax({ permission: "granted", reducedMotion: true }),
      false
    );
    assert.equal(
      shouldAnimateParallax({
        permission: "granted",
        reducedMotion: true,
        pointerFallbackActive: true,
      }),
      false
    );
  });

  it("권한이 있으면 움직인다", () => {
    assert.equal(
      shouldAnimateParallax({ permission: "granted", reducedMotion: false }),
      true
    );
  });

  it("권한이 거부돼도 포인터 폴백이 있으면 움직인다", () => {
    assert.equal(
      shouldAnimateParallax({
        permission: "denied",
        reducedMotion: false,
        pointerFallbackActive: true,
      }),
      true
    );
  });

  it("권한도 폴백도 없으면 정지한다 — 그래도 BREATHING 은 계속 돈다", () => {
    assert.equal(
      shouldAnimateParallax({ permission: "denied", reducedMotion: false }),
      false
    );
    assert.equal(
      shouldAnimateParallax({ permission: null, reducedMotion: false }),
      false
    );
    assert.equal(
      shouldAnimateParallax({ permission: "unavailable", reducedMotion: false }),
      false
    );
  });
});

describe("비-자이로 폴백 (포인터)", () => {
  const viewport = { width: 400, height: 800 };

  it("화면 중앙은 0 이다", () => {
    const s = pointerToGyroSample({ x: 200, y: 400 }, viewport);
    assert.equal(s.gamma, 0);
    assert.equal(s.beta, 0);
  });

  it("모서리는 최대 각도에 대응한다", () => {
    const s = pointerToGyroSample({ x: 400, y: 800 }, viewport);
    assert.equal(s.gamma, PARALLAX_DEFAULT.rangeDeg);
    assert.equal(s.beta, PARALLAX_DEFAULT.rangeDeg);
  });

  it("화면 밖 좌표도 최대치에서 잘린다", () => {
    const s = pointerToGyroSample({ x: 99999, y: -99999 }, viewport);
    assert.equal(s.gamma, PARALLAX_DEFAULT.rangeDeg);
    assert.equal(s.beta, -PARALLAX_DEFAULT.rangeDeg);
  });

  it("자이로와 같은 계산 경로를 탄다 — 상한이 자동으로 같다", () => {
    const t = createParallaxTracker();
    t.push(pointerToGyroSample({ x: 200, y: 400 }, viewport));
    const f = settle(t, pointerToGyroSample({ x: 400, y: 800 }, viewport) as {
      beta: number;
      gamma: number;
    });
    assert.ok(Math.abs(f.pet.x) <= PARALLAX_DEFAULT.petMaxPx + 1e-6);
  });

  it("0 크기 뷰포트에서도 나누기 오류가 없다", () => {
    const s = pointerToGyroSample({ x: 10, y: 10 }, { width: 0, height: 0 });
    assert.ok(Number.isFinite(s.gamma as number));
    assert.ok(Number.isFinite(s.beta as number));
  });
});
