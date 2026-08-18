/**
 * 재생 소스 진단 — "지금 화면에 붙어 있는 것이 진짜 생성 자산인가".
 *
 * 눈으로는 구분이 안 된다. 데모 클립(goya)도 개가 숨을 쉬고, CutoutIdleMotion 의
 * CSS 애니메이션도 숨을 쉬는 것처럼 보인다. 그래서 "BREATHING 이 나온다"는 관찰이
 * "진짜 펫 BREATH 영상이 나온다"의 증거가 되지 못한다.
 *
 * 이 모듈은 판정만 한다 — 재생 동작에는 관여하지 않는다.
 */

import { isDemoIdleUrl } from "./pending-generation.ts";

export type PlaybackSourceKind =
  /** 승격된 생성 자산 */
  | "real"
  /** 데모/폴백 mp4 (goya, /demo/) — 이 펫의 자산이 아니다 */
  | "fallback"
  /** 소스 없음 — 이 이벤트의 <video> 는 마운트되지 않는다 */
  | "missing";

export function classifyPlaybackSource(url: string | null | undefined): PlaybackSourceKind {
  const u = String(url ?? "").trim();
  if (!u) return "missing";
  return isDemoIdleUrl(u) ? "fallback" : "real";
}

export interface PlaybackSourceRow {
  id: string;
  kind: PlaybackSourceKind;
  url: string | null;
}

/** 표시 순서를 호출부가 정한다 — BREATHING 을 맨 위에 두고 싶기 때문이다. */
export function playbackSourceRows(
  entries: readonly (readonly [string, string | null | undefined])[]
): PlaybackSourceRow[] {
  return entries.map(([id, url]) => ({
    id,
    kind: classifyPlaybackSource(url),
    url: String(url ?? "").trim() || null,
  }));
}

/** 콘솔 한 덩어리로 읽히는 표. */
export function formatPlaybackSourceReport(rows: readonly PlaybackSourceRow[]): string {
  const width = rows.reduce((w, r) => Math.max(w, r.id.length), 0);
  return rows
    .map((r) => `${r.id.padEnd(width)} = ${r.kind.padEnd(8)} ${r.url ?? "—"}`)
    .join("\n");
}
