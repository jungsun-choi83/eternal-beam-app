/**
 * 속도 우선 누끼 (기본 ON). 품질 우선: VITE_CUTOUT_SPEED_MODE=0
 */
export const CUTOUT_SPEED_MODE =
  import.meta.env.VITE_CUTOUT_SPEED_MODE !== "0";

export const CUTOUT_WARMUP_MAX_MS = CUTOUT_SPEED_MODE ? 20_000 : 50_000;

/** 서버 1회 fast 누끼 상한 */
export const CUTOUT_SERVER_TIMEOUT_MS = CUTOUT_SPEED_MODE ? 90_000 : 240_000;

/** adaptive 2차 매팅 (장모) — 속도 모드에서는 OFF */
export const CUTOUT_AUTO_REFINE = !CUTOUT_SPEED_MODE;

/** 업로드 해상도 상한 */
export const CUTOUT_UPLOAD_MAX_EDGE_PX = CUTOUT_SPEED_MODE ? 960 : 1280;
