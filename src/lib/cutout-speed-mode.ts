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

/** 업로드 해상도 상한 (속도 모드: 720px — 홀로그램 표시엔 충분, 처리 단축) */
export const CUTOUT_UPLOAD_MAX_EDGE_PX = CUTOUT_SPEED_MODE ? 720 : 1280;

/** 브라우저 WASM 누끼 상한 (첫 실행·모델 다운로드 포함). 초과 시 서버 폴백 또는 오류 */
export const CLIENT_CUTOUT_TIMEOUT_MS = Number(
  import.meta.env.VITE_CLIENT_CUTOUT_TIMEOUT_MS ?? 240_000
);
