/**
 * 승인된 장면 → **프로바이더가 볼 한 장의 그림.**
 *
 * ── 왜 프론트에서 합성하는가 ────────────────────────────────────────────────
 * 배경 자산이 프론트에 있기 때문이다. 테마 배경은 `public/`(예: /demo/forest.mp4)
 * 에 있고 API 서버는 그 파일을 갖고 있지 않다 — backend/themes 에는 9개 테마 중
 * 6개만 있고, 프론트 registry 가 bgVideo 를 가진 세 테마(fresh_forest·beach·
 * snow_forest)는 **그 6개에 없다.** 서버에서 합성하려면 자산을 두 곳에 복제해야
 * 하고, 그러면 "미리보기와 생성 결과가 같다"를 자산 동기화에 의존하게 된다.
 *
 * 더 중요한 이유: 배치 수식이 여기 있다. 고객이 승인한 그림은 미리보기 화면이
 * 그린 바로 그 픽셀이고, 같은 코드로 다시 그리는 것이 "일치"의 가장 짧은 증명이다.
 *
 * ── 기하는 pet-grounding.ts 와 **같은 식**이다 ─────────────────────────────
 *   * 피사체 박스 높이 = 프레임 높이 × PET_BOX_HEIGHT_FRACTION
 *   * 박스는 프레임 바닥에 붙는다 (CSS `items-end`)
 *   * transform-origin: center bottom
 *   * transform: translate(posX, posY + shiftPct%) scale(scale)
 * 한쪽만 바꾸면 발이 지면에서 어긋난다.
 */

// 값 import 라 상대 경로 (node --test 가 별칭을 풀지 못한다).
import { PET_BOX_HEIGHT_FRACTION } from "./pet-grounding.ts";
import { SceneError, looksLikeCanvasTaint } from "./scene-errors.ts";

/** 프로바이더 입력 해상도. 16:9 — Luma/WAN 이 그대로 받는 비율이다. */
export const SCENE_W = 1280;
export const SCENE_H = 720;

export interface ScenePlacement {
  scale: number;
  posX: number;
  posY: number;
  /** 미리보기가 실제로 적용한 접지 보정(%). 다시 계산하지 않는다. */
  shiftPct: number;
}

/**
 * 미리보기 프레임 기준 좌표를 장면 캔버스 좌표로 옮기는 배율.
 *
 * posX/posY 는 **픽셀**이고 미리보기 프레임 크기에 상대적이다. 캔버스가 더 크면
 * 같은 픽셀 값이 더 작은 이동으로 보인다 — 그러면 승인한 위치와 어긋난다.
 */
export function placementScaleFactor(previewFrameHeight: number): number {
  const h = Number(previewFrameHeight);
  if (!Number.isFinite(h) || h <= 0) return 1;
  return SCENE_H / h;
}

type Drawable = CanvasImageSource & { width?: number; height?: number };

function sourceSize(src: Drawable): { w: number; h: number } {
  const anyimg = src as unknown as {
    naturalWidth?: number;
    naturalHeight?: number;
    videoWidth?: number;
    videoHeight?: number;
    width?: number;
    height?: number;
  };
  const w =
    anyimg.naturalWidth || anyimg.videoWidth || Number(anyimg.width) || SCENE_W;
  const h =
    anyimg.naturalHeight || anyimg.videoHeight || Number(anyimg.height) || SCENE_H;
  return { w, h };
}

/** 배경을 프레임에 **채워** 그린다(cover). contain 은 레터박스를 만든다. */
export function drawBackgroundCover(
  ctx: CanvasRenderingContext2D,
  bg: Drawable,
  w = SCENE_W,
  h = SCENE_H
): void {
  const { w: sw, h: sh } = sourceSize(bg);
  const ratio = Math.max(w / sw, h / sh);
  const dw = sw * ratio;
  const dh = sh * ratio;
  ctx.drawImage(bg, (w - dw) / 2, (h - dh) / 2, dw, dh);
}

/**
 * 펫을 배치한다 — `transform-origin: center bottom` 을 캔버스로 옮긴 것.
 *
 * CSS 는 요소를 그린 뒤 변환하지만 캔버스는 변환 후에 그린다. 그래서 원점을
 * (프레임 가로중앙, 프레임 바닥)으로 옮기고 그 자리에서 scale 한 뒤, 박스를
 * 원점 기준 왼쪽-위로 그린다. 순서를 바꾸면 확대할 때 발이 떠오른다.
 */
export function drawPetWithPlacement(
  ctx: CanvasRenderingContext2D,
  pet: Drawable,
  placement: ScenePlacement,
  opts: { w?: number; h?: number; placementFactor?: number } = {}
): void {
  const w = opts.w ?? SCENE_W;
  const h = opts.h ?? SCENE_H;
  const f = opts.placementFactor ?? 1;

  const { w: sw, h: sh } = sourceSize(pet);
  const boxH = h * PET_BOX_HEIGHT_FRACTION;
  const boxW = sh > 0 ? (sw / sh) * boxH : boxH;

  const dx = placement.posX * f;
  const dy = placement.posY * f + (placement.shiftPct / 100) * h;

  ctx.save();
  ctx.translate(w / 2 + dx, h + dy);
  ctx.scale(placement.scale, placement.scale);
  ctx.drawImage(pet, -boxW / 2, -boxH, boxW, boxH);
  ctx.restore();
}

async function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    // 원격 배경(커스텀)은 CORS 가 없으면 캔버스를 오염시켜 toBlob 이 던진다.
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`이미지를 불러오지 못했습니다: ${url}`));
    img.src = url;
  });
}

/** 영상 배경의 **첫 프레임**. 정지 장면이 필요하므로 한 장만 뽑는다. */
async function loadVideoFirstFrame(url: string): Promise<HTMLVideoElement> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.crossOrigin = "anonymous";
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    const fail = () => reject(new Error(`배경 영상을 불러오지 못했습니다: ${url}`));
    v.onerror = fail;
    v.onloadeddata = () => {
      // seek 이 끝나야 첫 프레임이 캔버스에 그려진다.
      const done = () => resolve(v);
      if (v.readyState >= 2 && v.currentTime > 0) return done();
      v.onseeked = done;
      try {
        v.currentTime = 0.04;
      } catch {
        done();
      }
    };
    v.src = url;
  });
}

function looksLikeVideo(url: string): boolean {
  const p = url.split("?")[0].split("#")[0].toLowerCase();
  return p.endsWith(".mp4") || p.endsWith(".webm") || p.endsWith(".mov");
}

/** 배경 주소 → 그릴 수 있는 소스. 영상이면 첫 프레임. */
export async function loadBackgroundSource(url: string): Promise<Drawable> {
  return looksLikeVideo(url) ? await loadVideoFirstFrame(url) : await loadImage(url);
}

export interface ComposeSceneInput {
  /** 배경 주소. */
  backgroundUrl: string | null;
  /**
   * 고객이 배경을 **명시적으로 골랐는가.**
   *
   * true 면 그 배경으로 만들지 못할 때 **던진다** — 검정 판으로 조용히 떨어지면
   * 고객이 고른 적 없는 그림에 유료 생성이 돌아간다. 실제로 그랬다.
   * false(레거시: 배경 선택 자체가 없는 흐름)일 때만 단색 폴백이 허용된다.
   */
  requireBackground?: boolean;
  /** 누끼 PNG 주소(투명 배경). */
  petCutoutUrl: string;
  placement: ScenePlacement;
  /** 미리보기 프레임 높이 — posX/posY 를 캔버스 좌표로 옮기는 데 쓴다. */
  previewFrameHeight?: number;
}

/**
 * 승인된 장면을 한 장의 PNG 로 굽는다.
 *
 * PNG 를 쓰는 이유: 프로바이더 입력은 한 번 더 JPEG 로 평탄화되는데, 그 전에
 * 우리가 먼저 JPEG 압축을 하면 털 경계에 링잉이 두 번 쌓인다.
 */
export async function composeSceneImage(
  input: ComposeSceneInput
): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = SCENE_W;
  canvas.height = SCENE_H;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("캔버스를 만들 수 없습니다.");

  const required = input.requireBackground === true;

  ctx.fillStyle = "#000000";
  ctx.fillRect(0, 0, SCENE_W, SCENE_H);

  // 배경을 고른 고객에게 검정 판을 주지 않는다. 못 그리면 **멈춘다.**
  if (!input.backgroundUrl) {
    if (required) {
      throw new SceneError(
        "BACKGROUND_LOAD_FAILED",
        "선택한 배경의 주소를 찾지 못했습니다."
      );
    }
  } else {
    try {
      drawBackgroundCover(ctx, await loadBackgroundSource(input.backgroundUrl));
    } catch (e) {
      if (required) {
        throw new SceneError(
          looksLikeCanvasTaint(e)
            ? "CUSTOM_BACKGROUND_CORS_FAILED"
            : "BACKGROUND_LOAD_FAILED",
          "배경을 불러오지 못했습니다."
        );
      }
      /* 레거시 흐름에서만 단색 폴백 */
    }
  }

  let pet: HTMLImageElement;
  try {
    pet = await loadImage(input.petCutoutUrl);
  } catch (e) {
    throw new SceneError("SCENE_PREPARATION_FAILED", "누끼를 불러오지 못했습니다.");
  }
  drawPetWithPlacement(ctx, pet, input.placement, {
    placementFactor: input.previewFrameHeight
      ? placementScaleFactor(input.previewFrameHeight)
      : 1,
  });

  return await new Promise<Blob>((resolve, reject) => {
    try {
      canvas.toBlob(
        (b) =>
          b
            ? resolve(b)
            : reject(
                new SceneError(
                  "SCENE_PREPARATION_FAILED",
                  "장면 이미지를 만들지 못했습니다."
                )
              ),
        "image/png"
      );
    } catch (e) {
      // toBlob 은 캔버스가 오염됐을 때 **동기로** 던진다(SecurityError).
      reject(
        new SceneError(
          looksLikeCanvasTaint(e)
            ? "CUSTOM_BACKGROUND_CORS_FAILED"
            : "SCENE_PREPARATION_FAILED",
          "장면 이미지를 만들지 못했습니다."
        )
      );
    }
  });
}
