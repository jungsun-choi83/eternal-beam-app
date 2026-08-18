"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createPackedAlphaScratch,
  drawPackedAlphaVideo,
  drawPackedRgbHalfOnly,
  isLikelyPackedAlphaSource,
  isPackedAlphaVideo,
} from "@/lib/packed-alpha-canvas";
import {
  type ReturnProfile,
  clampReturnStartScale,
  computeReturnFrame,
  returnProfileTotalMs,
} from "@/lib/action-return-transition";
import {
  SEAM_EPSILON_S,
  SEAM_WAIT_MAX_MS,
  type PetRuntimeTrigger,
  type RuntimeEventDef,
  type RuntimeEventId,
  type RuntimeEventSources,
  decideTrigger,
  mountableEvents,
  returnProfileFor,
} from "@/lib/pet-runtime-events";

interface IdleLoopVideoProps {
  /** BREATH(아이들) 소스 — 항상 loop 재생된다. */
  src: string;
  /**
   * 1회 재생 런타임 이벤트들의 소스 표 — 액션(COME_CLOSER)과 앞으로의 아이들
   * 이벤트가 **같은 재생 인프라**를 공유한다. lib/pet-runtime-events.ts 에
   * **등록된** id 만 유효하다.
   *
   * 소스가 붙은 이벤트마다 <video> 를 하나씩 마운트하고, trigger(id) 로 한 번
   * 재생한 뒤 자동으로 BREATHING 으로 돌아온다.
   *
   * preload 는 이벤트 정의가 정한다 — 새로 등록해도 기본이 "none" 이라 가만히
   * 두면 대역폭·디코더가 늘지 않는다.
   */
  eventSources?: RuntimeEventSources;
  /** 이벤트 재생이 시작/종료될 때 알림 (UI 힌트용). */
  onActionStateChange?: (playing: boolean) => void;
  /** 트리거 핸들을 부모에게 넘긴다. trigger("COME_CLOSER") 처럼 쓴다. */
  actionTriggerRef?: React.MutableRefObject<PetRuntimeTrigger | null>;
  className?: string;
  style?: React.CSSProperties;
  /** metadata = 빠른 첫 프레임, auto = 전체 프리로드 */
  preload?: "none" | "metadata" | "auto";
  /** true(기본): packed alpha / Luma 블랙배경 제거 후 투명 합성 */
  transparentComposite?: boolean;
  /**
   * 클립 하단의 빈 배경 비율을 1회 측정해 알려 준다(0~1).
   * 부모가 이 값만큼 피사체를 내려 실제 발이 테마 접지선에 닿게 한다.
   */
  onFeetMarginChange?: (bottomMargin: number) => void;
}

type RenderMode = "packed" | "blackkey" | "raw";

const COMPOSITE_FAIL_THRESHOLD = 8;

/**
 * 생성 영상 하단에 비어 있는(배경만 있는) 비율의 기본값.
 * 실측: Wan 480x832 ≈ 0.175, Luma 720x1280 ≈ 0.139 → 그 사이값을 안전한 폴백으로 둔다.
 * 측정에 실패하거나 측정 전 첫 프레임에서 쓰인다.
 */
export const DEFAULT_FEET_BOTTOM_MARGIN = 0.15;

/**
 * 액션 마지막 프레임 직전에 멈추기 위한 여유(초).
 *
 * `ended` 를 기다리지 않는 이유: 브라우저가 마지막 한두 프레임을 건너뛰고 ended 를
 * 쏘는 경우가 있어, 정작 붙잡아 두고 싶은 "도착" 프레임이 이미 지나간 뒤가 된다.
 * 재생 중 매 프레임 남은 시간을 보고 직접 멈추면 항상 같은 프레임에서 선다.
 */
const ACTION_TAIL_EPSILON_S = 0.06;

/**
 * `stalled` 이후 재생이 되살아나길 기다리는 유예.
 *
 * stalled 는 "영구 실패"가 아니라 "데이터가 아직 안 왔다"는 뜻이다. 예전에는 이걸
 * 즉시 BREATH 복귀 트리거로 썼기 때문에, 버퍼링 한 번에 액션이 이유 없이 끊겼다.
 */
const ACTION_STALL_GRACE_MS = 2500;

/** 측정 샘플 가로 해상도. 행별 통계만 필요하므로 작게 잡는다. */
const FEET_SAMPLE_WIDTH = 192;
/** 한 행이 "피사체"로 인정받는 데 필요한 최소 픽셀 수 (샘플 가로 대비). */
const FEET_MIN_ROW_RATIO = 0.006;
/** H.264 링잉/디더 노이즈 배제 — 연속 N행이 조건을 만족해야 발끝으로 인정. */
const FEET_MIN_CONSECUTIVE_ROWS = 3;

/**
 * 프레임 아래쪽의 "빈 배경" 비율을 측정한다.
 *
 * 반환: (frameHeight - feetRow) / frameHeight  (0~1), 측정 실패 시 null.
 *
 * 배경 판정은 렌더러가 쓰는 것과 같은 방식이다 — 네 모서리 평균 휘도로 밝은 배경/
 * 어두운 배경을 정하고(estimateCornerBackgroundLuminance), 그 수준에 가까운 픽셀을
 * 배경으로 본다(removeNearBackgroundAlpha 와 동일한 기준). 덕분에 검정 배경(Luma/Wan
 * 기본)과 흰 배경(어두운 강아지) 모두에서 동작한다.
 *
 * 단순히 "맨 아래 비배경 픽셀"을 찾으면 압축 노이즈 한 점에 걸려 값이 0에 가까워진다.
 * 그래서 (1) 한 행에 최소 픽셀 수를 요구하고 (2) 그런 행이 연속 3개일 때만 발끝으로 본다.
 */
function measureFeetBottomMargin(
  video: HTMLVideoElement,
  frameW: number,
  frameH: number
): number | null {
  if (!frameW || !frameH) return null;

  const sw = Math.min(FEET_SAMPLE_WIDTH, frameW);
  const sh = Math.max(1, Math.round((frameH / frameW) * sw));

  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  // 보간을 끄면 이웃 픽셀이 섞여 배경이 밝아지는 일이 없다.
  ctx.imageSmoothingEnabled = false;

  try {
    ctx.drawImage(video, 0, 0, frameW, frameH, 0, 0, sw, sh);
    const bgLum = estimateCornerBackgroundLuminance(ctx, sw, sh);
    const isBrightBg = bgLum >= 128;
    const delta = 18;
    const { data } = ctx.getImageData(0, 0, sw, sh);

    const minPerRow = Math.max(2, Math.round(sw * FEET_MIN_ROW_RATIO));
    let consecutive = 0;

    for (let y = sh - 1; y >= 0; y--) {
      let subjectPixels = 0;
      const rowStart = y * sw * 4;
      for (let x = 0; x < sw; x++) {
        const i = rowStart + x * 4;
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        const isBackground = isBrightBg ? lum >= bgLum - delta : lum <= bgLum + delta;
        if (!isBackground) subjectPixels += 1;
      }

      if (subjectPixels >= minPerRow) {
        consecutive += 1;
        if (consecutive >= FEET_MIN_CONSECUTIVE_ROWS) {
          // 연속 구간의 가장 아래 행이 발끝이다.
          const feetRow = y + FEET_MIN_CONSECUTIVE_ROWS - 1;
          const margin = (sh - 1 - feetRow) / sh;
          if (!Number.isFinite(margin) || margin < 0 || margin > 0.6) return null;
          return margin;
        }
      } else {
        consecutive = 0;
      }
    }
  } catch {
    // cross-origin 등으로 getImageData 가 막히면 폴백을 쓴다.
    return null;
  }
  return null;
}

/**
 * 프레임에서 피사체가 세로로 차지하는 비율(0~1). 측정 실패 시 null.
 *
 * measureFeetBottomMargin 과 **같은 배경 판정**(모서리 휘도 기준 + 연속 행 규칙)을
 * 쓴다. 다른 기준을 쓰면 두 측정값이 서로 안 맞아 접지와 복귀 배율이 따로 논다.
 *
 * 쓰임: 액션의 마지막 프레임에서 개가 화면의 몇 %를 차지하는지와, BREATH 에서 몇 %를
 * 차지하는지를 각각 재서 그 비율을 복귀 시작 배율로 쓴다. 상수로 고정하면 접근이
 * 짧은 클립에서는 BREATH 가 너무 크게, 긴 클립에서는 너무 작게 등장한다.
 */
function measureSubjectHeightFraction(
  video: HTMLVideoElement,
  frameW: number,
  frameH: number
): number | null {
  if (!frameW || !frameH) return null;

  const sw = Math.min(FEET_SAMPLE_WIDTH, frameW);
  const sh = Math.max(1, Math.round((frameH / frameW) * sw));

  const canvas = document.createElement("canvas");
  canvas.width = sw;
  canvas.height = sh;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return null;
  ctx.imageSmoothingEnabled = false;

  try {
    ctx.drawImage(video, 0, 0, frameW, frameH, 0, 0, sw, sh);
    const bgLum = estimateCornerBackgroundLuminance(ctx, sw, sh);
    const isBrightBg = bgLum >= 128;
    const delta = 18;
    const { data } = ctx.getImageData(0, 0, sw, sh);

    const minPerRow = Math.max(2, Math.round(sw * FEET_MIN_ROW_RATIO));
    const subjectRow: boolean[] = new Array(sh);
    for (let y = 0; y < sh; y++) {
      let subjectPixels = 0;
      const rowStart = y * sw * 4;
      for (let x = 0; x < sw; x++) {
        const i = rowStart + x * 4;
        const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        const isBackground = isBrightBg ? lum >= bgLum - delta : lum <= bgLum + delta;
        if (!isBackground) subjectPixels += 1;
      }
      subjectRow[y] = subjectPixels >= minPerRow;
    }

    // 위/아래 양쪽에서 "연속 N행" 규칙으로 경계를 찾는다 — 압축 노이즈 한 점에
    // 걸려 피사체가 프레임 전체를 채우는 것처럼 보이는 것을 막는다.
    const firstRun = (from: number, to: number, step: number): number | null => {
      let consecutive = 0;
      for (let y = from; step > 0 ? y <= to : y >= to; y += step) {
        if (subjectRow[y]) {
          consecutive += 1;
          if (consecutive >= FEET_MIN_CONSECUTIVE_ROWS) {
            return y - step * (FEET_MIN_CONSECUTIVE_ROWS - 1);
          }
        } else {
          consecutive = 0;
        }
      }
      return null;
    };

    const top = firstRun(0, sh - 1, 1);
    const bottom = firstRun(sh - 1, 0, -1);
    if (top == null || bottom == null || bottom <= top) return null;

    const fraction = (bottom - top + 1) / sh;
    if (!Number.isFinite(fraction) || fraction <= 0.05 || fraction > 1) return null;
    return fraction;
  } catch {
    // cross-origin 등으로 getImageData 가 막히면 폴백 배율을 쓴다.
    return null;
  }
}

/**
 * 액션의 마지막 프레임과 BREATH 의 피사체 크기 비 → BREATH 가 등장할 배율.
 *
 * null 이면 호출부가 폴백 상수를 쓴다. packed(vstack) 소스는 상/하단 절반 판정이
 * 필요해 건너뛴다 — measureFeet 과 같은 규칙이다.
 */
function measureReturnStartScale(
  action: HTMLVideoElement,
  idle: HTMLVideoElement,
  mode: RenderMode
): number | null {
  if (mode === "packed") return null;
  const aw = action.videoWidth;
  const ah = action.videoHeight;
  const iw = idle.videoWidth;
  const ih = idle.videoHeight;
  if (!aw || !ah || !iw || !ih) return null;
  const actionFraction = measureSubjectHeightFraction(action, aw, ah);
  const idleFraction = measureSubjectHeightFraction(idle, iw, ih);
  if (actionFraction == null || idleFraction == null || idleFraction <= 0) return null;
  return actionFraction / idleFraction;
}

function estimateCornerBackgroundLuminance(
  ctx: CanvasRenderingContext2D,
  w: number,
  h: number
): number {
  // Sample small patches in 4 corners (before we zero alpha).
  // If corners are bright => the video background is bright; else dark.
  const s = Math.max(2, Math.floor(Math.min(w, h) / 20));
  const corners: Array<[number, number]> = [
    [0, 0],
    [Math.max(0, w - s), 0],
    [0, Math.max(0, h - s)],
    [Math.max(0, w - s), Math.max(0, h - s)],
  ];

  let sum = 0;
  let count = 0;
  for (const [cx, cy] of corners) {
    const { data } = ctx.getImageData(cx, cy, s, s);
    for (let i = 0; i < data.length; i += 4) {
      const r = data[i];
      const g = data[i + 1];
      const b = data[i + 2];
      const lum = 0.299 * r + 0.587 * g + 0.114 * b;
      sum += lum;
      count += 1;
    }
  }
  return count > 0 ? sum / count : 0;
}

/** α 가 이 값 미만이면 0. 1/α 폭주 방지. */
const ALPHA_CUTOFF = 0.12;
/** 배경↔피사체 최소 휘도 간격. 대비 낮은 클립에서 α 가 튀는 걸 막는다. */
const MIN_LUM_SPAN = 60;
/** "완전히 덮인" 피사체 휘도로 볼 분위수. 실측상 p85 가 복원 오차 최소(6.3). */
const FG_PERCENTILE = 0.85;
/** 피사체 후보가 없을 때의 안전 기본 간격. */
const DEFAULT_LUM_SPAN = 140;
/** 침식 반경: 480px 폭 기준 3px. 실제로는 영역 크기에 비례시켜 스케일 무관하게 만든다. */
const EROSION_RADIUS_PER_PX = 3 / 480;
const EROSION_RADIUS_MIN = 1;
const EROSION_RADIUS_MAX = 6;

/**
 * 검정(또는 흰색) 배경 클립을 테마 위로 합성하기 위한 매트 생성.
 *
 * 예전에는 이진 판정이었다: alpha = (lum <= bgLum + 18) ? 0 : 255.
 * 그래서 11~65%만 덮인 경계 픽셀이 100% 불투명으로 그려졌고, 그 RGB 는 털색이
 * 배경 쪽으로 섞인 값(실측 평균 (93,63,48), R-B=+45)이라 밝은 테마 위에서
 * 갈색 테두리로 보였다. 검정 위에서는 주변도 검정이라 티가 안 났을 뿐이다.
 *
 * 그렇다고 휘도만으로 소프트 α 를 만들면 안 된다 — 어두운 내부(코·눈·입)와
 * "절반만 덮인 경계"가 같은 휘도라 구분이 안 되고, 실측상 몸통의 48.6%가
 * 반투명해진다. 그래서 공간 정보를 아주 조금만 쓴다:
 *
 *   M    = lum 임계 통과 = 실루엣 (오늘과 동일)
 *   core = erode(M, r)  → α=255, RGB 그대로  (아무리 어두워도 내부는 불투명)
 *   band = M - core     → 소프트 α + 배경 오염 제거
 *   그 외               → α=0
 *
 * 경계 밴드에서는 observed = α·true + (1-α)·bg 가 성립하므로(실측 확인:
 * observed(136,116,97)/α0.557 = (244,208,174) ≈ 내부 (244,205,171))
 * true = (observed - (1-α)·bg) / α 로 되돌린다.
 */
function removeNearBackgroundAlpha(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  bgLuminance: number,
  delta = 18
) {
  const ix = Math.round(x);
  const iy = Math.round(y);
  const iw = Math.max(1, Math.round(w));
  const ih = Math.max(1, Math.round(h));
  const imageData = ctx.getImageData(ix, iy, iw, ih);
  const { data } = imageData;
  const isBrightBg = bgLuminance >= 128;
  const n = iw * ih;

  // ── 1패스: 실루엣 M + 피사체 휘도 히스토그램 ────────────────────────────
  const mask = new Uint8Array(n);
  const hist = new Uint32Array(256);
  let candidates = 0;
  for (let p = 0; p < n; p++) {
    const i = p * 4;
    const lum = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
    const inside = isBrightBg ? lum < bgLuminance - delta : lum > bgLuminance + delta;
    if (inside) {
      mask[p] = 1;
      hist[lum < 0 ? 0 : lum > 255 ? 255 : Math.round(lum)] += 1;
      candidates += 1;
    }
  }

  // ── 1.5패스: 테두리 flood fill 로 "둘러싸인 구멍" 되살리기 ───────────────
  // 동공·입안 그림자·콧구멍은 휘도가 임계값 이하라 M 에서 빠지고, 그 결과 테마가
  // 강아지를 뚫고 비쳐 보였다(실측 939px = 실루엣의 1.11%).
  //
  // 프레임 테두리에서 배경만 타고 8방향으로 번져 나간다. 거기서 닿지 못한 배경
  // 픽셀은 피사체에 완전히 둘러싸인 구멍이므로 M 에 되돌린다.
  // 다리 사이·꼬리 아래처럼 바깥 배경과 이어진 틈은 테두리에서 닿으므로 그대로 둔다.
  // 8방향을 쓰는 이유: 대각선으로만 이어진 통로도 "바깥과 연결됨"으로 인정해
  // 실제로는 열려 있는 틈을 구멍으로 오인해 메우는 일을 막기 위함.
  const reached = new Uint8Array(n);
  const stack = new Int32Array(n - candidates > 0 ? n - candidates : 1);
  let sp = 0;
  const pushIfBg = (p: number) => {
    if (!mask[p] && !reached[p]) {
      reached[p] = 1;
      stack[sp++] = p;
    }
  };
  for (let xx = 0; xx < iw; xx++) {
    pushIfBg(xx);
    pushIfBg((ih - 1) * iw + xx);
  }
  for (let yy = 0; yy < ih; yy++) {
    pushIfBg(yy * iw);
    pushIfBg(yy * iw + iw - 1);
  }
  while (sp > 0) {
    const p = stack[--sp];
    const py = (p / iw) | 0;
    const px = p - py * iw;
    for (let dy = -1; dy <= 1; dy++) {
      const ny = py + dy;
      if (ny < 0 || ny >= ih) continue;
      for (let dx = -1; dx <= 1; dx++) {
        const nx = px + dx;
        if (nx < 0 || nx >= iw || (dx === 0 && dy === 0)) continue;
        pushIfBg(ny * iw + nx);
      }
    }
  }
  for (let p = 0; p < n; p++) {
    // 배경인데 테두리에서 닿지 못했다 = 둘러싸인 구멍 → 전경으로 편입.
    if (!mask[p] && !reached[p]) mask[p] = 1;
  }

  // 적응형 fgLum. 고정 상수(예: 215)는 어두운 강아지를 과하게 밝힌다.
  let span = DEFAULT_LUM_SPAN;
  if (candidates > 0) {
    // 검정 배경 → 밝은 쪽 p85, 흰 배경 → 어두운 쪽 p15. 둘 다 오름차순 누적.
    const target = Math.floor(candidates * (isBrightBg ? 1 - FG_PERCENTILE : FG_PERCENTILE));
    let acc = 0;
    let fgLum = isBrightBg ? 0 : 255;
    for (let v = 0; v < 256; v++) {
      acc += hist[v];
      if (acc > target) {
        fgLum = v;
        break;
      }
    }
    span = Math.abs(fgLum - bgLuminance);
  }
  if (span < MIN_LUM_SPAN) span = MIN_LUM_SPAN;

  // ── 2패스: 분리형 이진 침식으로 core 구하기 (O(n), 추가 캔버스 없음) ────
  // 반경은 영역 크기에 비례 — 480p/720p 어디서 그리든 물리적 경계 폭이 비슷해진다.
  let radius = Math.round(Math.min(iw, ih) * EROSION_RADIUS_PER_PX);
  if (radius < EROSION_RADIUS_MIN) radius = EROSION_RADIUS_MIN;
  if (radius > EROSION_RADIUS_MAX) radius = EROSION_RADIUS_MAX;

  const need = radius + 1; // 자기 자신 포함 연속 길이
  const run = new Uint16Array(n);
  const rowEroded = new Uint8Array(n);

  for (let yy = 0; yy < ih; yy++) {
    const base = yy * iw;
    let c = 0;
    for (let xx = 0; xx < iw; xx++) {
      c = mask[base + xx] ? c + 1 : 0;
      run[base + xx] = c;
    }
    c = 0;
    for (let xx = iw - 1; xx >= 0; xx--) {
      c = mask[base + xx] ? c + 1 : 0;
      rowEroded[base + xx] = run[base + xx] >= need && c >= need ? 1 : 0;
    }
  }

  const core = new Uint8Array(n);
  for (let xx = 0; xx < iw; xx++) {
    let c = 0;
    for (let yy = 0; yy < ih; yy++) {
      const p = yy * iw + xx;
      c = rowEroded[p] ? c + 1 : 0;
      run[p] = c;
    }
    c = 0;
    for (let yy = ih - 1; yy >= 0; yy--) {
      const p = yy * iw + xx;
      c = rowEroded[p] ? c + 1 : 0;
      core[p] = run[p] >= need && c >= need ? 1 : 0;
    }
  }

  // ── 3패스: core = 불투명 / band = 소프트 α + 오염 제거 / 그 외 = 투명 ──
  for (let p = 0; p < n; p++) {
    const i = p * 4;
    if (!mask[p]) {
      data[i + 3] = 0;
      continue;
    }
    if (core[p]) {
      // 내부 — 휘도와 무관하게 불투명, RGB 는 손대지 않는다.
      data[i + 3] = 255;
      continue;
    }

    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const lum = 0.299 * r + 0.587 * g + 0.114 * b;
    let alpha = (isBrightBg ? bgLuminance - lum : lum - bgLuminance) / span;
    if (alpha <= 0) {
      data[i + 3] = 0;
      continue;
    }
    if (alpha > 1) alpha = 1;
    if (alpha < ALPHA_CUTOFF) {
      data[i + 3] = 0;
      continue;
    }
    if (alpha < 1) {
      // true = (observed - (1-α)·bg) / α. 두 배경 모두 무채색이라 bgRGB=(bgLum,bgLum,bgLum).
      const bgTerm = (1 - alpha) * bgLuminance;
      const inv = 1 / alpha;
      const nr = (r - bgTerm) * inv;
      const ng = (g - bgTerm) * inv;
      const nb = (b - bgTerm) * inv;
      data[i] = nr < 0 ? 0 : nr > 255 ? 255 : nr;
      data[i + 1] = ng < 0 ? 0 : ng > 255 ? 255 : ng;
      data[i + 2] = nb < 0 ? 0 : nb > 255 ? 255 : nb;
      data[i + 3] = Math.round(alpha * 255);
    } else {
      data[i + 3] = 255;
    }
  }
  ctx.putImageData(imageData, ix, iy);
}

function fitFrameRect(
  cw: number,
  ch: number,
  frameW: number,
  frameH: number,
  alignBottom = true
) {
  const aspect = frameW / frameH;
  let drawW = cw;
  let drawH = drawW / aspect;
  if (drawH > ch) {
    drawH = ch;
    drawW = drawH * aspect;
  }
  const dx = (cw - drawW) / 2;
  const dy = alignBottom ? ch - drawH : (ch - drawH) / 2;
  return { dx, dy, drawW, drawH };
}

function measureWrapSize(wrap: HTMLDivElement): { cw: number; ch: number } {
  let cw = wrap.clientWidth;
  let ch = wrap.clientHeight;
  if (cw > 0 && ch > 0) return { cw, ch };

  const parent = wrap.parentElement;
  if (parent) {
    cw = cw || parent.clientWidth;
    ch = ch || parent.clientHeight;
  }
  return { cw, ch };
}

function videoCrossOrigin(src: string): "" | "anonymous" {
  if (typeof window === "undefined") return "anonymous";
  try {
    const u = new URL(src, window.location.href);
    return u.origin !== window.location.origin ? "anonymous" : "";
  } catch {
    return "";
  }
}

/** Luma idle 루프 — vstack packed mp4(상단 RGB·하단 알파) 또는 블랙배경 mp4를 투명 PET 레이어로 합성 */
export function IdleLoopVideo({
  src,
  eventSources,
  onActionStateChange,
  actionTriggerRef,
  className = "",
  style,
  preload = "metadata",
  transparentComposite = true,
  onFeetMarginChange,
}: IdleLoopVideoProps) {
  const wrapRef = useRef<HTMLDivElement>(null);
  // videoRef 는 **현재 활성 소스**를 가리킨다. 렌더 루프와 모드 판정 코드는
  // 예전 그대로 videoRef 만 읽으면 되므로 아이들 로직이 전혀 바뀌지 않는다.
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const idleVideoRef = useRef<HTMLVideoElement>(null);

  // ── 런타임 이벤트 레지스트리 배선 ─────────────────────────────────────────
  // 소스가 붙은 **등록된** 이벤트만 <video> 를 갖는다. 액션이든 아이들 이벤트든
  // 같은 배선을 쓴다 — 이벤트가 늘어도 이 배열이 길어질 뿐 상태 기계는 그대로다.
  const sources = eventSources ?? {};
  const mountedEvents = mountableEvents(sources);
  /** 마운트된 이벤트 집합의 안정적인 식별자 — effect 의존성으로 쓴다.
   *  mountedEvents 는 매 렌더 새 배열이라 그대로 넣으면 effect 가 계속 재실행된다. */
  const eventsKey = mountedEvents.map((e) => `${e.id}=${sources[e.id]}`).join("|");
  /** 이벤트 id → 그 이벤트의 <video>. 콜백 ref 로 채운다. */
  const actionElsRef = useRef(new Map<RuntimeEventId, HTMLVideoElement>());
  const setActionEl = useCallback((id: RuntimeEventId, el: HTMLVideoElement | null) => {
    if (el) actionElsRef.current.set(id, el);
    else actionElsRef.current.delete(id);
  }, []);
  const actionElFor = useCallback(
    (id: RuntimeEventId | null | undefined): HTMLVideoElement | null =>
      (id && actionElsRef.current.get(id)) || null,
    []
  );

  const onActionStateChangeRef = useRef(onActionStateChange);
  onActionStateChangeRef.current = onActionStateChange;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const scratchRef = useRef(createPackedAlphaScratch());
  const blackkeyScratchRef = useRef<HTMLCanvasElement | null>(null);
  // 액션 레이어용 스크래치는 **별도**다. 디졸브 동안 두 소스를 같은 프레임에 그리는데,
  // 하나를 돌려 쓰면 해상도가 다른 두 클립 사이에서 매 프레임 캔버스를 재할당하게 된다.
  const actionScratchRef = useRef(createPackedAlphaScratch());
  const actionBlackkeyScratchRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef(0);
  const modeRef = useRef<RenderMode>("raw");
  const modeDetectedRef = useRef(false);
  const failCountRef = useRef(0);
  const [useRawFallback, setUseRawFallback] = useState(false);
  const useRawFallbackRef = useRef(false);
  useRawFallbackRef.current = useRawFallback;
  const packedSourceRef = useRef(isLikelyPackedAlphaSource(src));
  const crossOrigin = videoCrossOrigin(src);
  const feetMeasuredRef = useRef(false);
  const onFeetMarginChangeRef = useRef(onFeetMarginChange);
  onFeetMarginChangeRef.current = onFeetMarginChange;

  const triggerRawFallback = useCallback(() => {
    if (packedSourceRef.current || isLikelyPackedAlphaSource(src)) {
      if (import.meta.env.DEV) {
        console.warn("[IdleLoopVideo] packed composite degraded — keeping canvas (no raw vstack)", src);
      }
      return;
    }
    modeRef.current = "raw";
    setUseRawFallback(true);
    if (import.meta.env.DEV) {
      console.warn("[IdleLoopVideo] canvas composite failed — falling back to raw video", src);
    }
  }, [src]);

  // ── idle ⇄ action 상태 기계 (forest-experience-screen 의 검증된 패턴) ──────
  // 소스를 모두 미리 붙여 두고 videoRef 만 바꾼다. src 를 교체하지 않으므로
  // 리로드로 인한 검은 프레임(플리커)이 생기지 않는다.
  //
  // 복귀만은 포인터 교체 하나로 끝내지 않는다 — 액션은 클로즈업으로 끝나고 BREATH 는
  // 전신이라, 한 프레임에 바꾸면 순간이동처럼 보인다. 자세한 근거는
  // lib/action-return-transition.ts 참고.
  //
  // 상태는 **하나의 유니온**이다. 예전에는 boolean(actionPlayingRef)과 전환 객체
  // (returnRef)가 따로 있어서 "재생 중"과 "복귀 중"이 동시에 참인 구간을 호출부마다
  // 다르게 조합해야 했다 — 이벤트가 늘면 반드시 어긋나는 모양이었다.
  //
  // 단계 이름이 EVENT_* 인 이유: 이 자리를 지나가는 것이 액션만이 아니다. 앞으로의
  // 아이들 이벤트(BLINKING 등)도 같은 경로를 쓴다. ACTION_* 이라고 부르면 눈 깜빡임을
  // 액션으로 취급하게 되고, 그게 정확히 이번에 바로잡은 오해다.
  type PlaybackState =
    | { phase: "IDLE" }
    | {
        /**
         * 이음매 대기 — 트리거는 받았지만 BREATHING 이 휴지 자세에 올 때까지 아직
         * 시작하지 않았다. 화면에는 여전히 BREATHING 만 보인다.
         */
        phase: "EVENT_PENDING_SEAM";
        event: RuntimeEventDef;
        /** 대기 시작 시각 — SEAM_WAIT_MAX_MS 상한 판정용. */
        requestedAt: number;
      }
    | { phase: "EVENT_PLAYING"; event: RuntimeEventDef }
    | {
        phase: "EVENT_RETURNING";
        event: RuntimeEventDef;
        /** 이벤트를 정지시킨 시각(performance.now). 유일한 시계다. */
        startedAt: number;
        /** 이 이벤트의 복귀 타이밍 프로파일. */
        profile: ReturnProfile;
        /** BREATHING 이 등장할 배율. 배율 브리지를 안 쓰면 1. */
        startScale: number;
        /** BREATHING 재생을 이미 시작시켰는가 (hold→crossfade 경계에서 1회). */
        idleResumed: boolean;
      };

  const playbackRef = useRef<PlaybackState>({ phase: "IDLE" });
  /** 현재 활성(재생 또는 복귀) 이벤트. IDLE 이면 null. */
  const activeEvent = useCallback((): RuntimeEventDef | null => {
    const s = playbackRef.current;
    return s.phase === "IDLE" ? null : s.event;
  }, []);
  /** rAF 가 멈춘 채(탭 비활성 등) 전환이 끝나지 않는 것을 막는 최후 보루. */
  const returnWatchdogRef = useRef<number | null>(null);
  const stallTimerRef = useRef<number | null>(null);

  const clearReturnTimers = useCallback(() => {
    if (returnWatchdogRef.current != null) {
      window.clearTimeout(returnWatchdogRef.current);
      returnWatchdogRef.current = null;
    }
    if (stallTimerRef.current != null) {
      window.clearTimeout(stallTimerRef.current);
      stallTimerRef.current = null;
    }
  }, []);

  /** 전환 없이 즉시 BREATH 로. 실패 경로(error/abort/소스 제거)에서만 쓴다. */
  const startIdle = useCallback(() => {
    const idle = idleVideoRef.current;
    const action = actionElFor(activeEvent()?.id);
    if (!idle) return;
    const wasPlaying = playbackRef.current.phase !== "IDLE";
    playbackRef.current = { phase: "IDLE" };
    clearReturnTimers();
    if (action) {
      action.pause();
      action.currentTime = 0;
    }
    videoRef.current = idle;
    // 결정적 착지 — 어디서 멈췄든 항상 쉬는 자세(첫 프레임)에서 다시 시작한다.
    // IDLE 프롬프트가 "클립은 동일한 쉬는 자세로 시작하고 끝난다"를 보장하므로
    // 0 이 곧 휴지점이다. 예전처럼 일시정지 지점에서 이어 붙이면 들숨 한가운데로
    // 복귀하는 등 매번 다른 자세로 착지했다.
    try {
      idle.currentTime = 0;
    } catch {
      /* 시크 불가(메타데이터 미도착) — 그대로 재생한다 */
    }
    void idle.play().catch(() => {
      /* autoplay policy */
    });
    if (wasPlaying) onActionStateChangeRef.current?.(false);
  }, [clearReturnTimers, activeEvent, actionElFor]);

  /** 전환 완료 — BREATH 를 유일한 활성 소스로 되돌린다. */
  const finishReturn = useCallback(() => {
    const idle = idleVideoRef.current;
    const action = actionElFor(activeEvent()?.id);
    const wasPlaying = playbackRef.current.phase !== "IDLE";
    playbackRef.current = { phase: "IDLE" };
    clearReturnTimers();
    if (action) {
      action.pause();
      action.currentTime = 0;
    }
    if (idle) videoRef.current = idle;
    if (wasPlaying) onActionStateChangeRef.current?.(false);
  }, [clearReturnTimers, activeEvent, actionElFor]);

  /**
   * 액션 도착 → 복귀 전환 시작. 마지막 프레임에서 정지시키고 hold 에 들어간다.
   *
   * 여기서 BREATH 를 재생시키지 않는다 — hold 가 끝나는 경계에서 renderFrame 이
   * 한 번만 시작시킨다. 지금 틀면 hold 동안 BREATH 가 보이지 않는 채로 흘러가
   * 결정적 착지가 깨진다.
   */
  const beginReturn = useCallback(() => {
    const state = playbackRef.current;
    if (state.phase !== "EVENT_PLAYING") return; // 이미 복귀 중이거나 IDLE
    const def = state.event;
    const action = actionElFor(def.id);
    const idle = idleVideoRef.current;
    if (!action || !idle) return;

    const profile = returnProfileFor(def.returnPolicy);

    // 즉시 복귀해야 하는 경우:
    //   - 정책이 immediate 이거나 전환 길이가 0
    //   - 캔버스 합성이 아니라 두 레이어를 겹쳐 그릴 수단이 없다
    //     (이벤트 <video> 는 캔버스 분기에서만 마운트되므로 사실상 방어 코드다)
    if (
      returnProfileTotalMs(profile) <= 0 ||
      !transparentComposite ||
      useRawFallbackRef.current
    ) {
      startIdle();
      return;
    }

    action.pause(); // 마지막 프레임 고정 — 캔버스는 정지된 프레임을 계속 그린다

    // 배율 브리지를 쓰는 정책에서만 실측한다. seam-aligned 는 프레이밍이 같으므로
    // 측정 자체가 불필요하고, getImageData 두 번을 아낀다.
    const startScale = profile.scaleBridge
      ? clampReturnStartScale(measureReturnStartScale(action, idle, modeRef.current))
      : 1;

    playbackRef.current = {
      phase: "EVENT_RETURNING",
      event: def,
      startedAt: performance.now(),
      profile,
      startScale,
      idleResumed: false,
    };

    // rAF 가 돌지 않는 동안(탭 백그라운드)에도 상태가 영원히 멈추지 않게 한다.
    clearReturnTimers();
    returnWatchdogRef.current = window.setTimeout(() => {
      if (playbackRef.current.phase === "EVENT_RETURNING") finishReturn();
    }, returnProfileTotalMs(profile) + 500);

    if (import.meta.env.DEV) {
      console.info(
        `[${def.id}] 복귀 전환 시작 policy=${def.returnPolicy} startScale=`,
        startScale.toFixed(3)
      );
    }
  }, [transparentComposite, startIdle, finishReturn, clearReturnTimers, actionElFor]);

  /**
   * 런타임 이벤트 트리거 — 진입 규칙은 전부 lib/pet-runtime-events.ts 의
   * decideTrigger 가 쥔다. 여기서는 판정 결과를 실행만 한다. 액션이든 아이들
   * 이벤트든 늘어나도 이 함수는 그대로다.
   */
  /**
   * 실제 재생 시작 — 이음매 판정이 이미 끝난 뒤에만 불린다.
   * 선점(preempt)일 경우 **이전 이벤트의 <video> 를 반드시 멈춘다** — 안 그러면
   * 화면 밖에서 계속 돌면서 ended 를 쏘고, 그 ended 가 새 이벤트를 복귀시킨다.
   */
  const startEvent = useCallback(
    (def: RuntimeEventDef, previous: RuntimeEventDef | null) => {
      const idle = idleVideoRef.current;
      const actionEl = actionElFor(def.id);
      if (!idle || !actionEl) return;

      if (previous && previous.id !== def.id) {
        const prevEl = actionElFor(previous.id);
        if (prevEl) {
          prevEl.pause();
          prevEl.currentTime = 0;
        }
        if (import.meta.env.DEV) {
          console.info(`[${def.id}] ${previous.id} 를 선점`);
        }
      }

      playbackRef.current = { phase: "EVENT_PLAYING", event: def };
      clearReturnTimers();
      idle.pause();
      actionEl.currentTime = 0;
      videoRef.current = actionEl;
      onActionStateChangeRef.current?.(true);
      void actionEl
        .play()
        .then(() => {
          if (import.meta.env.DEV) {
            console.info(`[${def.id}] 재생 시작 readyState=`, actionEl.readyState);
          }
        })
        .catch((err) => {
          // 재생 거부(자동재생 정책·로드 실패) → 즉시 BREATH 복귀 (멈춘 화면 방지)
          if (import.meta.env.DEV) {
            console.warn(`[${def.id}] play() 실패 → BREATH 복귀`, err);
          }
          startIdle();
        });
    },
    [startIdle, clearReturnTimers, actionElFor]
  );

  const trigger = useCallback<PetRuntimeTrigger>(
    (requestedId) => {
      const actionEl = actionElFor(requestedId);
      const state = playbackRef.current;

      const decision = decideTrigger({
        phase: state.phase,
        currentEventId: state.phase === "IDLE" ? null : state.event.id,
        requestedEventId: requestedId,
        hasSource: !!actionEl?.src,
      });

      if (!decision.accepted) {
        if (import.meta.env.DEV) {
          console.info(`[${requestedId}] 트리거 무시 — ${decision.reason}`);
        }
        return;
      }

      const def = decision.event;
      const previous = state.phase === "IDLE" ? null : state.event;

      // entryPolicy=immediate → 지금 바로. 사용자가 유발한 사건은 기다리면 안 된다.
      if (def.entryPolicy === "immediate") {
        startEvent(def, previous);
        return;
      }

      // entryPolicy=wait-for-seam → BREATHING 이 휴지 자세에 올 때까지 미룬다.
      // 대기 중에는 아무것도 바꾸지 않는다 — 화면은 계속 BREATHING 이다.
      // 실제 판정과 시작은 renderFrame 이 매 프레임 확인한다.
      if (previous && previous.id !== def.id) {
        // 선점 대상이 이미 재생 중이었다면 지금 멈춘다(대기 중 화면에 남지 않게).
        const prevEl = actionElFor(previous.id);
        if (prevEl) {
          prevEl.pause();
          prevEl.currentTime = 0;
        }
        const idle = idleVideoRef.current;
        if (idle) {
          videoRef.current = idle;
          void idle.play().catch(() => {
            /* autoplay policy */
          });
        }
      }
      clearReturnTimers();
      playbackRef.current = {
        phase: "EVENT_PENDING_SEAM",
        event: def,
        requestedAt: performance.now(),
      };
      if (import.meta.env.DEV) {
        console.info(`[${def.id}] 이음매 대기 시작 (최대 ${SEAM_WAIT_MAX_MS}ms)`);
      }
    },
    [startEvent, clearReturnTimers, actionElFor]
  );

  // 부모가 더블탭 등에서 호출할 수 있게 핸들을 넘긴다.
  // 재생 가능한 액션이 하나도 없으면 null 이다 — 예전 actionSrc 가 비었을 때와 같다.
  const hasAnyAction = mountedEvents.length > 0;
  useEffect(() => {
    if (!actionTriggerRef) return;
    actionTriggerRef.current = hasAnyAction ? trigger : null;
    return () => {
      actionTriggerRef.current = null;
    };
  }, [actionTriggerRef, trigger, hasAnyAction]);

  // 액션 영상은 **사용자 조작 전까지 항상 정지 상태**여야 한다.
  // 프리로드/브라우저 자동재생 정책이 무엇을 하든, 재생 중이 아니면 강제로 멈추고
  // 첫 프레임으로 되돌린다. 캔버스 활성 소스도 BREATH 로 고정한다.
  useEffect(() => {
    const idle = idleVideoRef.current;
    const entries = [...actionElsRef.current.entries()];
    if (entries.length === 0) return;

    const cleanups: Array<() => void> = [];
    for (const [id, actionEl] of entries) {
      const pinPaused = () => {
        const state = playbackRef.current;
        // 이 액션이 지금 재생 중이면 건드리지 않는다.
        if (state.phase === "EVENT_PLAYING" && state.event.id === id) return;
        // 복귀 전환 중에는 액션이 **일부러** 도착 프레임에서 정지해 있다. 여기서
        // currentTime 을 0 으로 되돌리면 붙잡아 두려던 클로즈업 대신 첫 프레임이
        // 나타난다 — hold 의 의미가 정확히 반대로 뒤집힌다.
        if (state.phase === "EVENT_RETURNING" && state.event.id === id) return;
        if (!actionEl.paused) actionEl.pause();
        if (actionEl.currentTime !== 0) actionEl.currentTime = 0;
        // 다른 액션이 활성일 때 캔버스 소스를 빼앗지 않는다.
        if (idle && state.phase === "IDLE") videoRef.current = idle;
      };

      pinPaused();
      // 로드 진행 중에도 계속 확인 — preload 가 재생으로 이어지지 않게.
      const events = ["loadedmetadata", "loadeddata", "canplay", "play"] as const;
      for (const ev of events) actionEl.addEventListener(ev, pinPaused);
      cleanups.push(() => {
        for (const ev of events) actionEl.removeEventListener(ev, pinPaused);
      });
    }
    return () => {
      for (const c of cleanups) c();
    };
  }, [eventsKey]);

  // 액션 종료/오류 → BREATH 복귀. **타이머가 아니라 실제 재생 완료 기준**이다.
  //
  // 이벤트별로 복귀 방식이 다르다:
  //   ended        → 정상 도착. 부드러운 전환(hold + 디졸브)을 태운다.
  //                  보통은 renderFrame 의 꼬리 감지가 먼저 잡지만, 그게 놓쳤을
  //                  때(탭 비활성 등)를 위한 안전망으로 남긴다.
  //   error/abort  → 클립이 깨졌다. 디졸브할 화면이 없으므로 즉시 복귀.
  //   stalled      → **실패가 아니다.** 유예를 준 뒤에도 안 살아나면 그때 복귀.
  useEffect(() => {
    const entries = [...actionElsRef.current.entries()];
    if (entries.length === 0) return;

    const cancelStallTimer = () => {
      if (stallTimerRef.current != null) {
        window.clearTimeout(stallTimerRef.current);
        stallTimerRef.current = null;
      }
    };

    /** 이 이벤트가 지금 재생 중(복귀 전이)인가 — 남의 DOM 이벤트에 반응하지 않기 위해. */
    const isPlaying = (id: RuntimeEventId) => {
      const s = playbackRef.current;
      return s.phase === "EVENT_PLAYING" && s.event.id === id;
    };

    const cleanups: Array<() => void> = [];
    for (const [id, actionEl] of entries) {
      const onEnded = () => {
        if (isPlaying(id)) beginReturn();
      };
      const onFailure = () => {
        if (isPlaying(id)) startIdle();
      };
      const onStalled = () => {
        if (!isPlaying(id)) return;
        if (stallTimerRef.current != null) return; // 이미 유예 중
        const stalledAt = actionEl.currentTime;
        stallTimerRef.current = window.setTimeout(() => {
          stallTimerRef.current = null;
          if (!isPlaying(id)) return;
          // 유예 동안 재생이 조금이라도 진행됐으면 살아난 것이다.
          if (actionEl.currentTime > stalledAt + 0.01) return;
          if (import.meta.env.DEV) {
            console.warn(`[${id}] stalled 유예 초과 → BREATH 복귀`);
          }
          startIdle();
        }, ACTION_STALL_GRACE_MS);
      };

      actionEl.addEventListener("ended", onEnded);
      actionEl.addEventListener("error", onFailure);
      actionEl.addEventListener("abort", onFailure);
      actionEl.addEventListener("stalled", onStalled);
      actionEl.addEventListener("playing", cancelStallTimer);
      actionEl.addEventListener("timeupdate", cancelStallTimer);
      cleanups.push(() => {
        actionEl.removeEventListener("ended", onEnded);
        actionEl.removeEventListener("error", onFailure);
        actionEl.removeEventListener("abort", onFailure);
        actionEl.removeEventListener("stalled", onStalled);
        actionEl.removeEventListener("playing", cancelStallTimer);
        actionEl.removeEventListener("timeupdate", cancelStallTimer);
      });
    }
    return () => {
      for (const c of cleanups) c();
      cancelStallTimer();
    };
  }, [eventsKey, startIdle, beginReturn]);

  // 언마운트 시 타이머 정리.
  useEffect(() => clearReturnTimers, [clearReturnTimers]);

  // 활성 액션의 소스가 사라지면 즉시 BREATH 로 되돌린다.
  useEffect(() => {
    const state = playbackRef.current;
    if (state.phase === "IDLE") return;
    const stillMounted = mountedEvents.some((a) => a.id === state.event.id);
    if (!stillMounted) startIdle();
  }, [eventsKey, mountedEvents, startIdle]);

  const renderFrame = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    const idle = idleVideoRef.current;
    const state = playbackRef.current;
    const action = state.phase === "IDLE" ? null : actionElsRef.current.get(state.event.id) ?? null;

    // ── 이음매 게이트: 대기 중인 이벤트를 언제 시작할지 판정한다 ────────────────
    //
    // **이 "이음매"는 측정된 호흡 위상이 아니라 BREATH 클립의 루프 경계다.**
    // 근거: IDLE_COMMON_CONSTRAINT 가 BREATH 에 "동일한 휴지 자세로 시작하고 끝난다"를
    // 요구하므로 t≈0 (= t≈duration) 이 휴지 자세다. 클립 안의 중간 휴지점들은
    // 실측 없이는 알 수 없어 여기서 찾지 못한다 — 그래서 상한을 둔다.
    if (state.phase === "EVENT_PENDING_SEAM") {
      const waited = performance.now() - state.requestedAt;
      const t = idle?.currentTime ?? 0;
      const dur = idle?.duration ?? 0;
      const atSeam =
        !!idle &&
        Number.isFinite(dur) &&
        dur > 0 &&
        (t <= SEAM_EPSILON_S || dur - t <= SEAM_EPSILON_S);

      if (atSeam) {
        startEvent(state.event, null);
      } else if (waited >= SEAM_WAIT_MAX_MS) {
        // 이음매가 오지 않는다(클립이 길거나 BREATH 가 아직 안 돌고 있다).
        // 이벤트를 조용히 삼키는 것보다 품질을 조금 포기하고 재생하는 편이 낫다.
        if (import.meta.env.DEV) {
          console.warn(`[${state.event.id}] 이음매 대기 시간 초과 → 그대로 시작(품질 저하)`);
        }
        startEvent(state.event, null);
      }
    }

    // ── 꼬리 감지: 재생 중인 이벤트가 끝에 닿으면 마지막 프레임에서 세운다 ──────
    // `ended` 를 기다리지 않는다. timeupdate 는 ~250ms 간격이라 60ms 창을 놓치고,
    // ended 는 마지막 프레임을 이미 지난 뒤에 온다. 매 프레임 확인이 가장 정확하다.
    if (
      action &&
      state.phase === "EVENT_PLAYING" &&
      videoRef.current === action &&
      !action.paused &&
      Number.isFinite(action.duration) &&
      action.duration > 0 &&
      action.duration - action.currentTime <= ACTION_TAIL_EPSILON_S
    ) {
      beginReturn();
    }

    // ── 이번 프레임에 그릴 레이어 결정 ────────────────────────────────────────
    // 위에서 상태가 바뀌었을 수 있으므로 다시 읽는다.
    const transition = playbackRef.current;
    let frame = null as ReturnType<typeof computeReturnFrame> | null;
    if (transition.phase === "EVENT_RETURNING") {
      frame = computeReturnFrame(
        performance.now() - transition.startedAt,
        transition.startScale,
        transition.profile
      );

      // hold → crossfade 경계에서 BREATH 를 **한 번만** 결정적으로 되감아 재생한다.
      if (frame.phase !== "hold" && !transition.idleResumed && idle) {
        transition.idleResumed = true;
        try {
          idle.currentTime = 0;
        } catch {
          /* 시크 불가 — 그대로 재생 */
        }
        void idle.play().catch(() => {
          /* autoplay policy */
        });
      }

      if (frame.phase === "done") {
        finishReturn();
        frame = null;
      }
    }

    const video = videoRef.current;
    if (
      !video ||
      !canvas ||
      !wrap ||
      !transparentComposite ||
      modeRef.current === "raw" ||
      !modeDetectedRef.current
    ) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    if (video.readyState < 2) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    const { cw, ch } = measureWrapSize(wrap);
    if (cw <= 0 || ch <= 0) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pw = Math.round(cw * dpr);
    const ph = Math.round(ch * dpr);
    if (canvas.width !== pw || canvas.height !== ph) {
      canvas.width = pw;
      canvas.height = ph;
    }

    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) {
      rafRef.current = requestAnimationFrame(renderFrame);
      return;
    }

    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.clearRect(0, 0, cw, ch);

    /**
     * 한 소스를 캔버스에 합성한다.
     *
     * 배율은 **맞춰진 사각형의 아래-가운데**를 기준으로 건다. 화면 중앙 기준으로
     * 걸면 크기가 변할 때 발이 같이 떠올라, 방금 맞춘 접지(pet-grounding)가
     * 전환 중에만 깨진다. `.preview-subject-layer { transform-origin: center bottom }`
     * 과 같은 기준이다. 카메라·배경은 건드리지 않는다 — 이건 펫 레이어 전용이다.
     */
    const drawLayer = (
      source: HTMLVideoElement,
      alpha: number,
      scale: number,
      packedScratch: ReturnType<typeof createPackedAlphaScratch>,
      blackkeyRef: React.MutableRefObject<HTMLCanvasElement | null>
    ) => {
      if (alpha <= 0.001 || source.readyState < 2) return;
      const svw = source.videoWidth;
      const svh = source.videoHeight;
      if (!svw || !svh) return;

      const frameH = modeRef.current === "packed" ? Math.floor(svh / 2) : svh;
      const { dx, dy, drawW, drawH } = fitFrameRect(cw, ch, svw, frameH);

      ctx.save();
      ctx.globalAlpha = alpha;
      if (scale !== 1) {
        const ax = dx + drawW / 2;
        const ay = dy + drawH;
        ctx.translate(ax, ay);
        ctx.scale(scale, scale);
        ctx.translate(-ax, -ay);
      }

      try {
        if (modeRef.current === "packed") {
          try {
            drawPackedAlphaVideo(ctx, source, dx, dy, drawW, drawH, packedScratch);
          } catch (packedErr) {
            if (import.meta.env.DEV) {
              console.warn("[IdleLoopVideo] packed alpha read failed — RGB half only", packedErr);
            }
            drawPackedRgbHalfOnly(ctx, source, dx, dy, drawW, drawH, packedScratch);
          }
        } else {
          const iw = Math.max(1, Math.round(drawW));
          const ih = Math.max(1, Math.round(drawH));
          let scratch = blackkeyRef.current;
          if (!scratch) {
            scratch = document.createElement("canvas");
            blackkeyRef.current = scratch;
          }
          if (scratch.width !== iw || scratch.height !== ih) {
            scratch.width = iw;
            scratch.height = ih;
          }
          const sctx = scratch.getContext("2d", { willReadFrequently: true });
          if (!sctx) throw new Error("blackkey scratch context unavailable");
          sctx.imageSmoothingEnabled = true;
          sctx.imageSmoothingQuality = "high";
          sctx.clearRect(0, 0, iw, ih);
          sctx.drawImage(source, 0, 0, iw, ih);
          const bgLum = estimateCornerBackgroundLuminance(sctx, iw, ih);
          removeNearBackgroundAlpha(sctx, 0, 0, iw, ih, bgLum);
          ctx.drawImage(scratch, dx, dy, drawW, drawH);
        }
      } finally {
        ctx.restore();
      }
    };

    try {
      if (frame && action && idle) {
        // 전환 중 — 두 소스를 같은 프레임에 겹쳐 그린다.
        // 액션을 먼저(뒤에) 깔고 BREATH 를 위에 얹는다: 나타나는 쪽이 위에 있어야
        // 디졸브 후반이 깨끗하다.
        drawLayer(action, frame.actionAlpha, frame.actionScale, actionScratchRef.current, actionBlackkeyScratchRef);
        drawLayer(idle, frame.idleAlpha, frame.idleScale, scratchRef.current, blackkeyScratchRef);
      } else {
        const isAction = video === action;
        drawLayer(
          video,
          1,
          1,
          isAction ? actionScratchRef.current : scratchRef.current,
          isAction ? actionBlackkeyScratchRef : blackkeyScratchRef
        );
      }
      failCountRef.current = 0;
    } catch (err) {
      failCountRef.current += 1;
      if (import.meta.env.DEV) {
        console.warn("[IdleLoopVideo] frame composite error", err);
      }
      if (failCountRef.current >= COMPOSITE_FAIL_THRESHOLD) {
        triggerRawFallback();
        return;
      }
    }

    rafRef.current = requestAnimationFrame(renderFrame);
  }, [transparentComposite, triggerRawFallback, beginReturn, finishReturn, startEvent]);

  useEffect(() => {
    failCountRef.current = 0;
    modeDetectedRef.current = false;
    setUseRawFallback(false);
    packedSourceRef.current = isLikelyPackedAlphaSource(src);
    scratchRef.current = createPackedAlphaScratch();
    feetMeasuredRef.current = false;
    blackkeyScratchRef.current = null;
  }, [src, transparentComposite]);

  // 액션 레이어 스크래치는 액션 소스 집합이 바뀔 때 버린다 — 크기·rgbOnTop 이
  // 캐시돼 있어, 해상도가 다른 클립으로 갈아타면 잘못된 값으로 그린다.
  // 액션 레이어는 한 번에 하나만 그려지므로 스크래치 한 쌍을 공유해도 된다.
  useEffect(() => {
    actionScratchRef.current = createPackedAlphaScratch();
    actionBlackkeyScratchRef.current = null;
  }, [eventsKey]);

  useEffect(() => {
    const el = idleVideoRef.current;
    if (!el) return;
    // 활성 소스 기본값 = BREATH. 액션이 활성이면 trigger 가 설정한 값을 유지.
    if (playbackRef.current.phase === "IDLE") videoRef.current = el;

    el.muted = true;
    el.playsInline = true;
    if (crossOrigin) el.crossOrigin = crossOrigin;
    else el.removeAttribute("crossorigin");
    packedSourceRef.current = isLikelyPackedAlphaSource(src);
    modeRef.current = transparentComposite
      ? packedSourceRef.current
        ? "packed"
        : "blackkey"
      : "raw";
    modeDetectedRef.current = transparentComposite ? packedSourceRef.current : true;

    const play = () => {
      void el.play().catch(() => {
        /* autoplay policy */
      });
    };

    const detectMode = () => {
      if (!transparentComposite) {
        modeRef.current = "raw";
        return;
      }
      const packed = isPackedAlphaVideo(el, src);
      modeRef.current = packed ? "packed" : "blackkey";
      modeDetectedRef.current = true;
      packedSourceRef.current = packed || isLikelyPackedAlphaSource(src);

      const vw = el.videoWidth;
      const vh = el.videoHeight;
      const wrap = wrapRef.current;
      if (vw && vh && wrap) {
        const frameH = modeRef.current === "packed" ? Math.floor(vh / 2) : vh;
        wrap.style.setProperty("--idle-aspect", `${vw} / ${frameH}`);
      }
    };

    const onVideoError = () => {
      if (import.meta.env.DEV) {
        console.warn("[IdleLoopVideo] video error", src, el.error);
      }
      if (!isLikelyPackedAlphaSource(src)) {
        triggerRawFallback();
      }
    };

    // 클립당 1회. loadeddata(readyState>=2, 첫 프레임 사용 가능) 시점에 재면
    // 캔버스가 실제로 그리기 시작하는 프레임과 같은 타이밍이라 보정 전/후 점프가
    // 눈에 띄지 않는다. 매 애니메이션 프레임에서 재지 않는다.
    const measureFeet = () => {
      if (feetMeasuredRef.current) return;
      if (el.readyState < 2) return;
      const vw = el.videoWidth;
      const vh = el.videoHeight;
      if (!vw || !vh) return;
      feetMeasuredRef.current = true;
      // packed(vstack 데모 에셋)은 상/하단 절반 판정이 필요해 건너뛴다 — 폴백 사용.
      if (modeRef.current === "packed") return;
      const margin = measureFeetBottomMargin(el, vw, vh);
      if (margin == null) return;
      wrapRef.current?.style.setProperty("--idle-feet-margin", String(margin));
      onFeetMarginChangeRef.current?.(margin);
    };

    play();
    el.addEventListener("loadeddata", play);
    el.addEventListener("loadeddata", measureFeet);
    el.addEventListener("loadedmetadata", detectMode);
    el.addEventListener("error", onVideoError);
    if (el.readyState >= 1) detectMode();
    if (el.readyState >= 2) measureFeet();

    return () => {
      el.removeEventListener("loadeddata", play);
      el.removeEventListener("loadeddata", measureFeet);
      el.removeEventListener("loadedmetadata", detectMode);
      el.removeEventListener("error", onVideoError);
    };
  }, [src, transparentComposite, triggerRawFallback, crossOrigin]);

  useEffect(() => {
    if (!transparentComposite || useRawFallback) return;

    const wrap = wrapRef.current;
    if (!wrap) return;

    const ro = new ResizeObserver(() => {
      /* renderFrame reads latest clientWidth/Height each tick */
    });
    ro.observe(wrap);
    if (wrap.parentElement) ro.observe(wrap.parentElement);

    rafRef.current = requestAnimationFrame(renderFrame);
    return () => {
      ro.disconnect();
      cancelAnimationFrame(rafRef.current);
    };
  }, [src, transparentComposite, useRawFallback, renderFrame]);

  const isPackedSrc = isLikelyPackedAlphaSource(src) || packedSourceRef.current;
  const useCanvasComposite = transparentComposite && (!useRawFallback || isPackedSrc);

  if (!useCanvasComposite) {
    return (
      <video
        ref={idleVideoRef}
        src={src}
        className={className}
        style={style}
        autoPlay
        loop
        muted
        playsInline
        preload={preload}
        {...(crossOrigin ? { crossOrigin } : {})}
      />
    );
  }

  return (
    <div
      ref={wrapRef}
      className={`idle-loop-video ${className}`}
      style={style}
      data-packed-alpha={packedSourceRef.current ? "" : undefined}
    >
      <video
        ref={idleVideoRef}
        src={src}
        className="idle-loop-video__source"
        tabIndex={-1}
        autoPlay
        loop
        muted
        playsInline
        preload={preload}
        {...(crossOrigin ? { crossOrigin } : {})}
        aria-hidden
      />
      {mountedEvents.map((def) => {
        const actionSrc = (sources[def.id] as string) ?? "";
        // crossOrigin 은 **소스별로** 정한다. 예전에는 BREATH 소스에서 계산한 값을
        // 액션에도 그대로 썼는데, 둘의 오리진이 다르면 액션 프레임이 캔버스를
        // 오염시켜 합성이 실패하고 raw 폴백으로 떨어졌다.
        const actionCrossOrigin = videoCrossOrigin(actionSrc);
        return (
          <video
            key={def.id}
            ref={(el) => setActionEl(def.id, el)}
            src={actionSrc}
            data-action={def.id}
            className="idle-loop-video__source"
            tabIndex={-1}
            /* 명시적으로 자동재생 금지 — trigger(id) 로만 재생된다.
               preload 정책은 액션 정의가 쥔다 (새 액션 기본값은 "none"). */
            autoPlay={false}
            muted
            playsInline
            preload={def.preload}
            {...(actionCrossOrigin ? { crossOrigin: actionCrossOrigin } : {})}
            aria-hidden
          />
        );
      })}
      <canvas ref={canvasRef} className="idle-loop-video__canvas" aria-hidden />
    </div>
  );
}
