/**
 * My Library 카드 정지 미리보기 — 순수 규칙 (DOM 없이 테스트 가능).
 *
 * 카드는 발행 자산(packed_alpha vstack: 상단 RGB·하단 알파)의 **첫 프레임 하나**를
 * 합성해 보여 준다. 재생하지 않고, 원본 프로바이더 영상도 쓰지 않는다.
 */

import { isLikelyPackedAlphaSource } from "./packed-alpha-canvas.ts";

export type PosterRenderMode = "packed" | "plain";

/** 명시 전달 포맷이 1순위, 없으면 파일명 규칙(`_packed.mp4`)으로 추정한다. */
export function posterRenderMode(deliveryFormat: string | null | undefined, src: string): PosterRenderMode {
  if (deliveryFormat === "packed_alpha") return "packed";
  if (!deliveryFormat && isLikelyPackedAlphaSource(src)) return "packed";
  return "plain";
}

/** 첫 프레임은 인코더 키프레임 잔상이 섞일 수 있어 아주 조금 뒤를 잡는다. */
export const POSTER_SEEK_SECONDS = 0.1;

export function posterSeekTime(duration: number): number {
  if (!Number.isFinite(duration) || duration <= 0) return 0;
  return Math.min(POSTER_SEEK_SECONDS, duration / 2);
}

/** 카드가 가벼워야 하므로 렌더 해상도를 제한한다 (CSS px × dpr, 상한 있음). */
export const POSTER_MAX_DPR = 2;
export const POSTER_MAX_WIDTH_PX = 640;

export function posterCanvasSize(cssW: number, cssH: number, dpr: number): { w: number; h: number } {
  const safeW = Math.max(1, Math.round(cssW));
  const safeH = Math.max(1, Math.round(cssH));
  const scale = Math.min(Math.max(1, dpr || 1), POSTER_MAX_DPR, POSTER_MAX_WIDTH_PX / safeW);
  return { w: Math.max(1, Math.round(safeW * scale)), h: Math.max(1, Math.round(safeH * scale)) };
}

/** 프레임을 잘라내지 않고(contain) 가운데에 놓는다 — 펫이 잘리지 않아야 한다. */
export function fitPosterFrame(cw: number, ch: number, frameW: number, frameH: number) {
  const aspect = frameW / frameH;
  let drawW = cw;
  let drawH = drawW / aspect;
  if (drawH > ch) {
    drawH = ch;
    drawW = drawH * aspect;
  }
  return { dx: (cw - drawW) / 2, dy: (ch - drawH) / 2, drawW, drawH };
}
