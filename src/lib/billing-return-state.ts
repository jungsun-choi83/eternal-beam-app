/**
 * 결제 왕복(Toss) 동안 화면 상태를 붙잡아 두는 스냅샷.
 *
 * 문제: Toss 는 결제창을 마치면 **페이지를 이동**시킨다. 새 문서가 뜨므로 React
 * state 가 통째로 사라지고, 앱은 첫 화면부터 다시 시작한다. 그래서 결제 직후
 * 사용자는 이미 만든 펫이 아니라 업로드 화면을 보게 됐다.
 *
 * 무엇을 저장하고 무엇을 저장하지 않는가 — 이 구분이 이 모듈의 핵심이다:
 *
 *   저장한다   screen    (어느 화면으로 돌아갈지)
 *              settings  (펫 위치·크기 — 접지가 어긋나면 눈에 띈다)
 *              contentId (같은 펫인지 확인하는 지문)
 *
 *   저장하지 않는다
 *              cutoutImage / 테마 / 파이프라인
 *              → 이미 sessionStorage·localStorage 에 있고, 화면들이 스스로 읽는다.
 *                여기서 또 저장하면 사본이 갈라지고, 누끼 data URL 은 용량 한도를
 *                넘길 수도 있다. **펫을 새로 만들지 않는 이유가 이것이다** —
 *                복원은 기존 자산을 가리키기만 한다.
 *
 * sessionStorage 를 쓴다: 탭 안의 리다이렉트는 넘어가고, 탭을 닫으면 사라진다.
 * 결제 왕복은 정확히 그 수명이다.
 */

/** 결제 후 돌아갈 수 있는 화면 — 기존 펫이 이미 재생 중인 곳들. */
export type BillingReturnScreen = "devicePlay" | "preview";

export interface BillingReturnSettings {
  scale: number;
  posX: number;
  posY: number;
}

export interface BillingReturnState {
  screen: BillingReturnScreen;
  settings: BillingReturnSettings;
  /** 스냅샷을 찍을 때의 펫(content) 지문. 복원 시 같은 펫인지 확인한다. */
  contentId: string | null;
}

export const BILLING_RETURN_KEY = "eternal_beam_billing_return_v1";

export function saveBillingReturnState(state: BillingReturnState): void {
  try {
    sessionStorage.setItem(BILLING_RETURN_KEY, JSON.stringify(state));
  } catch {
    /* 용량 초과 등 — 복원이 안 될 뿐, 결제를 막지는 않는다 */
  }
}

export function readBillingReturnState(): BillingReturnState | null {
  try {
    const raw = sessionStorage.getItem(BILLING_RETURN_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<BillingReturnState>;
    if (parsed.screen !== "devicePlay" && parsed.screen !== "preview") return null;
    const s = parsed.settings;
    if (!s || typeof s.scale !== "number") return null;
    return {
      screen: parsed.screen,
      settings: { scale: s.scale, posX: Number(s.posX) || 0, posY: Number(s.posY) || 0 },
      contentId: parsed.contentId ?? null,
    };
  } catch {
    return null;
  }
}

export function clearBillingReturnState(): void {
  try {
    sessionStorage.removeItem(BILLING_RETURN_KEY);
  } catch {
    /* ignore */
  }
}

/** 복원 판정에 필요한 파이프라인의 최소 모양. */
export interface PipelineLike {
  content_id?: string | null;
  idle_video_url?: string | null;
}

/**
 * 지금 이 스냅샷으로 복원해도 되는가 — **순수 판정**.
 *
 * 세 가지를 모두 만족해야 한다:
 *   1) 스냅샷이 있다
 *   2) 파이프라인이 살아 있고 **BREATHING 영상이 실제로 있다**
 *      (없으면 재생기가 마운트되지 않아 빈 화면으로 복원된다)
 *   3) 같은 펫이다 (content_id 일치)
 *
 * 하나라도 어긋나면 null → 호출부는 안전한 기본 경로(설정 화면)로 간다.
 * **틀린 펫을 복원하느니 설정 화면으로 가는 편이 낫다.**
 */
export function resolveBillingReturn(
  saved: BillingReturnState | null,
  pipeline: PipelineLike | null | undefined
): { screen: BillingReturnScreen; settings: BillingReturnSettings } | null {
  if (!saved) return null;
  if (!pipeline) return null;

  // BREATHING 이 없으면 devicePlay/preview 는 보여 줄 것이 없다.
  const idle = (pipeline.idle_video_url || "").trim();
  if (!idle) return null;

  // 스냅샷 이후 새 사진을 올렸다면 content_id 가 바뀐다 — 그때는 복원하지 않는다.
  if (saved.contentId && pipeline.content_id && saved.contentId !== pipeline.content_id) {
    return null;
  }

  return { screen: saved.screen, settings: saved.settings };
}
