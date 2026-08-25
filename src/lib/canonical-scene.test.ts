/**
 * 정본 장면 (Phase 19) — 프론트 쪽 계약.
 *
 * 지키는 것:
 *   * 장면 id 는 **결정적**이다 (같은 승인 → 같은 키 → 재과금 없음).
 *   * 장면은 콘텐츠에 묶인다 (다른 아이의 배경으로 생성하지 않는다).
 *   * 합성 기하가 미리보기(pet-grounding)와 **같은 식**이다.
 *   * 세 배경 갈래가 한 분기점에서만 갈린다.
 *   * 레거시 자산은 지금까지처럼 재생된다.
 */

import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveSceneId,
  isBackgroundType,
  sceneFormFields,
  sceneMatchesContent,
  type CanonicalScene,
} from "./canonical-scene.ts";
import {
  SCENE_H,
  SCENE_W,
  drawPetWithPlacement,
  placementScaleFactor,
} from "./scene-export.ts";
import { PET_BOX_HEIGHT_FRACTION, computeSubjectShiftPct } from "./pet-grounding.ts";
import {
  isBackgroundBaked,
  shouldRenderThemeBackdrop,
  shouldRunComposeVideo,
  shouldTransparentComposite,
} from "./baked-playback.ts";

const SCENE: CanonicalScene = {
  sceneId: "scene_x",
  contentId: "cid1",
  backgroundType: "theme",
  backgroundId: "fresh_forest",
  petScale: 1.1,
  petX: 8,
  petY: -3,
  floorY: 0.88,
  shiftPct: -7,
  sceneKeyframeUrl: "https://s/scene.png",
  backgroundBaked: true,
};

// ── 장면 id 는 결정적이다 ────────────────────────────────────────────────────

test("같은 승인은 같은 장면 id — 재과금의 근거", () => {
  const input = {
    contentId: "c1",
    backgroundType: "theme" as const,
    backgroundId: "fresh_forest",
    petScale: 1.2,
    petX: 10,
    petY: -5,
  };
  assert.equal(deriveSceneId(input), deriveSceneId(input));
});

test("배경이 다르면 다른 장면", () => {
  const base = {
    contentId: "c1",
    backgroundType: "theme" as const,
    backgroundId: "fresh_forest",
    petScale: 1,
    petX: 0,
    petY: 0,
  };
  assert.notEqual(
    deriveSceneId(base),
    deriveSceneId({ ...base, backgroundId: "beach" })
  );
});

test("배치가 다르면 다른 장면 — 위치를 바꿔 승인하면 실제로 다른 그림이다", () => {
  const base = {
    contentId: "c1",
    backgroundType: "theme" as const,
    backgroundId: "fresh_forest",
    petScale: 1,
    petX: 0,
    petY: 0,
  };
  assert.notEqual(deriveSceneId(base), deriveSceneId({ ...base, petX: 40 }));
  assert.notEqual(deriveSceneId(base), deriveSceneId({ ...base, petScale: 1.5 }));
});

test("배경 갈래가 다르면 다른 장면", () => {
  const base = {
    contentId: "c1",
    backgroundId: "x",
    petScale: 1,
    petX: 0,
    petY: 0,
  };
  const ids = (["original", "theme", "custom"] as const).map((backgroundType) =>
    deriveSceneId({ ...base, backgroundType })
  );
  assert.equal(new Set(ids).size, 3);
});

// ── 콘텐츠 결속 ──────────────────────────────────────────────────────────────

test("장면은 자기 콘텐츠에만 쓰인다 — 다른 아이의 배경으로 생성하지 않는다", () => {
  assert.equal(sceneMatchesContent(SCENE, "cid1"), true);
  assert.equal(sceneMatchesContent(SCENE, "cid2"), false);
  assert.equal(sceneMatchesContent(SCENE, ""), false);
  assert.equal(sceneMatchesContent(null, "cid1"), false);
});

test("배경 갈래 셋만 인정한다", () => {
  for (const t of ["original", "theme", "custom"]) {
    assert.equal(isBackgroundType(t), true, t);
  }
  for (const t of ["hologram", "", null, 3]) {
    assert.equal(isBackgroundType(t), false, String(t));
  }
});

test("폼 필드는 서버 파라미터 이름과 1:1", () => {
  const f = sceneFormFields(SCENE);
  assert.equal(f.scene_id, "scene_x");
  assert.equal(f.background_type, "theme");
  assert.equal(f.background_id, "fresh_forest");
  assert.equal(f.background_baked, "true");
  assert.equal(f.scene_keyframe_url, "https://s/scene.png");
});

// ── 합성 기하가 미리보기와 같다 ─────────────────────────────────────────────

/** drawImage 호출을 기록하는 최소 캔버스 대역. */
function fakeCtx() {
  const ops: Record<string, unknown>[] = [];
  let tx = 0;
  let ty = 0;
  let sx = 1;
  let sy = 1;
  return {
    ops,
    ctx: {
      save() {},
      restore() {},
      translate(x: number, y: number) {
        tx = x;
        ty = y;
        ops.push({ op: "translate", x, y });
      },
      scale(x: number, y: number) {
        sx = x;
        sy = y;
        ops.push({ op: "scale", x, y });
      },
      drawImage(_img: unknown, x: number, y: number, w: number, h: number) {
        ops.push({ op: "draw", x, y, w, h, tx, ty, sx, sy });
      },
    } as unknown as CanvasRenderingContext2D,
  };
}

const petImg = { naturalWidth: 400, naturalHeight: 400 } as unknown as CanvasImageSource;

test("피사체 박스 높이는 CSS 와 같은 비율 — 발이 지면에서 어긋나지 않는다", () => {
  const { ops, ctx } = fakeCtx();
  drawPetWithPlacement(ctx, petImg, { scale: 1, posX: 0, posY: 0, shiftPct: 0 });
  const draw = ops.find((o) => o.op === "draw")!;
  assert.equal(draw.h, SCENE_H * PET_BOX_HEIGHT_FRACTION);
});

test("원점은 center bottom — 확대해도 발이 뜨지 않는다", () => {
  const { ops, ctx } = fakeCtx();
  drawPetWithPlacement(ctx, petImg, { scale: 2, posX: 0, posY: 0, shiftPct: 0 });
  const t = ops.find((o) => o.op === "translate")!;
  assert.equal(t.x, SCENE_W / 2);
  assert.equal(t.y, SCENE_H, "원점이 프레임 바닥이 아니다");
  const draw = ops.find((o) => o.op === "draw")!;
  // 박스는 원점 기준 위로 그려진다(y = -boxH), 그래야 바닥이 고정된다.
  assert.equal(draw.y, -(SCENE_H * PET_BOX_HEIGHT_FRACTION));
  assert.equal(draw.sx, 2);
});

test("shiftPct 는 프레임 높이의 백분율로 적용된다 — pet-grounding 과 같은 규칙", () => {
  const shiftPct = computeSubjectShiftPct({ floorY: 0.88, feetMargin: 0.15 });
  const { ops, ctx } = fakeCtx();
  drawPetWithPlacement(ctx, petImg, { scale: 1, posX: 0, posY: 0, shiftPct });
  const t = ops.find((o) => o.op === "translate")!;
  assert.equal(t.y, SCENE_H + (shiftPct / 100) * SCENE_H);
});

test("미리보기 프레임이 작으면 배치 픽셀을 장면 좌표로 환산한다", () => {
  // 360px 프레임에서 +36px 이동은 프레임 높이의 10% 다. 장면에서도 10% 여야 한다.
  const f = placementScaleFactor(360);
  assert.equal(f, SCENE_H / 360);
  const { ops, ctx } = fakeCtx();
  drawPetWithPlacement(
    ctx,
    petImg,
    { scale: 1, posX: 36, posY: 0, shiftPct: 0 },
    { placementFactor: f }
  );
  const t = ops.find((o) => o.op === "translate")!;
  assert.equal(t.x, SCENE_W / 2 + SCENE_H * 0.1);
});

test("프레임 높이를 모르면 환산하지 않는다 (배율 1)", () => {
  assert.equal(placementScaleFactor(0), 1);
  assert.equal(placementScaleFactor(NaN), 1);
});

// ── 레거시 재생 ──────────────────────────────────────────────────────────────

test("표시가 없으면 레거시 — 기존 자산이 지금까지처럼 재생된다", () => {
  assert.equal(isBackgroundBaked(null), false);
  assert.equal(isBackgroundBaked(undefined), false);
  assert.equal(isBackgroundBaked({}), false);
  assert.equal(isBackgroundBaked({ background_baked: false }), false);
});

test("구운 자산은 키잉도 배경 레이어도 하지 않는다 — 배경 이중 적용 금지", () => {
  const baked = { backgroundBaked: true };
  assert.equal(isBackgroundBaked(baked), true);
  assert.equal(shouldTransparentComposite(baked), false);
  assert.equal(shouldRenderThemeBackdrop(baked), false);
  assert.equal(shouldRunComposeVideo(baked), false);
});

test("레거시 자산은 세 처리를 모두 그대로 받는다", () => {
  const legacy = { backgroundBaked: false };
  assert.equal(shouldTransparentComposite(legacy), true);
  assert.equal(shouldRenderThemeBackdrop(legacy), true);
  assert.equal(shouldRunComposeVideo(legacy), true);
});

test("snake_case 응답도 인식한다 — 서버가 그 모양으로 준다", () => {
  assert.equal(isBackgroundBaked({ background_baked: true }), true);
});
