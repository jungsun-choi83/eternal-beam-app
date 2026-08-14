/**
 * 프리미엄 액션(COME_CLOSER) → BREATH 복귀 전환의 **타이밍 계산**.
 *
 * 문제: 액션은 "얼굴·가슴이 화면을 채우는" 클로즈업으로 끝나도록 프롬프트가
 * 요구하는데(luma_prompts.py COME_CLOSER Beat 3), BREATH 첫 프레임은 원래 거리의
 * 전신이다. 예전 복귀는 활성 소스 포인터만 한 프레임에 바꿔치기했으므로
 * (idle-loop-video.tsx startIdle) 그 사이 3~5배의 외형 크기 차이가 1프레임 만에
 * 튀었다 — 같은 개가 아니라 **다른 숏으로 컷**한 것처럼 보였다.
 *
 * 해결은 세 박자다:
 *   1) 도착 프레임을 잠깐 붙잡아 감정적 정점에 머무는 시간을 준다,
 *   2) BREATH 를 액션이 끝난 **크기 근처에서** 등장시켜,
 *   3) 감속하며 원래 전신 크기로 되돌린다.
 * 그러면 컷이 아니라 "시선이 풀리며 물러나는" 것으로 읽힌다.
 *
 * 카메라·배경은 건드리지 않는다 — 여기서 나오는 배율은 펫 레이어에만 적용된다.
 *
 * 순수 함수만 둔다(DOM/React 없음) — `npm test` 가 그대로 로드한다.
 */

/**
 * 복귀 타이밍 프로파일 — 이벤트 정의의 returnPolicy 가 이 중 하나를 고른다.
 *
 * 하나의 상수 집합으로 모든 복귀를 처리할 수 없다. COME_CLOSER 는 **프레이밍이
 * 불연속**이라(클로즈업 → 전신) 긴 디졸브와 배율 브리지가 필요하지만, 눈 깜빡임처럼
 * 프레이밍이 같은 아이들 이벤트에 같은 처리를 하면 오히려 나빠진다 — 거의 동일한 두
 * 이미지를 600ms 동안 교차시키면 털·귀 가장자리에 이중상(ghosting)이 뜬다.
 * 닮은 클립일수록 디졸브가 더 잘 보인다.
 */
export interface ReturnProfile {
  /** 마지막 프레임을 붙잡아 두는 시간. */
  holdMs: number;
  /** 교차 디졸브 길이. 0 이면 즉시 컷. */
  crossfadeMs: number;
  /**
   * BREATH 를 확대된 상태에서 등장시켜 되돌릴 것인가.
   * false 면 배율은 항상 1 — 프레이밍이 같은 이벤트에는 배율 브리지가 해롭다.
   */
  scaleBridge: boolean;
}

/** 도착(마지막) 프레임을 붙잡아 두는 시간 — hold-and-dissolve 기준. */
export const ARRIVAL_HOLD_MS = 300;

/** 교차 디졸브 길이 — hold-and-dissolve 기준. */
export const RETURN_CROSSFADE_MS = 600;

/** 전환 전체 길이 — 액션이 끝나고 평소 BREATH 로 돌아오기까지. */
export const RETURN_TOTAL_MS = ARRIVAL_HOLD_MS + RETURN_CROSSFADE_MS;

/**
 * COME_CLOSER 용 — 도착을 붙잡고, 배율 브리지로 물러난다.
 * Phase 0 에서 검증된 값 그대로다.
 */
export const HOLD_AND_DISSOLVE: ReturnProfile = {
  holdMs: ARRIVAL_HOLD_MS,
  crossfadeMs: RETURN_CROSSFADE_MS,
  scaleBridge: true,
};

/**
 * 아이들 이벤트용 — 휴지 자세에서 휴지 자세로 돌아간다.
 *
 * hold 가 없다: 붙잡아 둘 "도착의 순간"이 없다. 배율 브리지도 없다: 프레이밍이
 * 애초에 같다. 남는 것은 아주 짧은 교차뿐이고, 이건 포즈가 미세하게 어긋났을 때의
 * 보험이다. 150ms 는 이중상이 인지되기 전에 끝나는 길이다.
 */
export const SEAM_ALIGNED: ReturnProfile = {
  holdMs: 0,
  crossfadeMs: 150,
  scaleBridge: false,
};

/** 즉시 컷 — 실패 경로용. 전환 없음. */
export const IMMEDIATE_RETURN: ReturnProfile = {
  holdMs: 0,
  crossfadeMs: 0,
  scaleBridge: false,
};

/** 프로파일의 전체 길이. 워치독·테스트가 쓴다. */
export function returnProfileTotalMs(p: ReturnProfile): number {
  return p.holdMs + p.crossfadeMs;
}

/**
 * BREATH 가 등장할 배율을 실측하지 못했을 때 쓰는 값.
 *
 * 현재 클립 실측(면적 +73% ≒ 선형 1.32배)에 맞춘 보수적인 기본값이다. 접근이
 * 더 가까워지도록 개선되면 실측 경로가 알아서 커진다.
 */
export const RETURN_START_SCALE_FALLBACK = 1.35;

/** 실측 배율 하한 — 1 미만이면 BREATH 가 확대가 아니라 축소로 등장한다. */
export const RETURN_START_SCALE_MIN = 1;
/** 실측 배율 상한 — 이상값이 들어와 BREATH 가 터무니없이 크게 시작하는 것 방지. */
export const RETURN_START_SCALE_MAX = 2.6;

/**
 * 디졸브 동안 액션에 남겨 두는 전진 모멘텀.
 * 사라지는 동안에도 아주 조금 더 다가오면 "멈춰서 사라졌다"는 느낌이 없어진다.
 */
export const ACTION_EXIT_SCALE_GAIN = 0.06;

export type ReturnPhase = "hold" | "crossfade" | "done";

export interface ReturnFrame {
  phase: ReturnPhase;
  /** COME_CLOSER 레이어. */
  actionAlpha: number;
  actionScale: number;
  /** BREATH 레이어. hold 구간에서는 alpha 0 — 아직 그리지 않는다. */
  idleAlpha: number;
  idleScale: number;
}

/** 감속 이징. 접근은 가속했으니 해제는 감속해야 대칭이 맞는다. */
export function easeOutCubic(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return 1 - Math.pow(1 - x, 3);
}

/** 실측/추정 배율을 안전 범위로 강제한다. */
export function clampReturnStartScale(scale: number | null | undefined): number {
  if (typeof scale !== "number" || !Number.isFinite(scale)) {
    return RETURN_START_SCALE_FALLBACK;
  }
  return Math.min(RETURN_START_SCALE_MAX, Math.max(RETURN_START_SCALE_MIN, scale));
}

/**
 * 전환 시작(= 이벤트 정지 시점) 이후 경과 시간 → 두 레이어의 알파·배율.
 *
 * 시계가 하나뿐이다(경과 시간 하나). hold 와 crossfade 를 따로 재면 둘 사이에
 * 오차가 끼는데, 여기서는 경계가 프로파일 값으로 결정되므로 어긋날 수가 없다.
 *
 * @param elapsedMs   이벤트를 정지시킨 순간부터의 경과(ms).
 * @param startScale  BREATH 가 등장할 배율. profile.scaleBridge 가 false 면 무시되고 1 이다.
 * @param profile     복귀 타이밍. 기본값은 Phase 0 의 COME_CLOSER 동작 그대로.
 */
export function computeReturnFrame(
  elapsedMs: number,
  startScale: number,
  profile: ReturnProfile = HOLD_AND_DISSOLVE
): ReturnFrame {
  // 배율 브리지를 쓰지 않는 프로파일에서는 등장 배율이 항상 1 이다 —
  // 실측값이 잘못 흘러들어와도 아이들 이벤트가 확대되어 튀어나올 수 없다.
  const scale = profile.scaleBridge ? clampReturnStartScale(startScale) : 1;
  const exitGain = profile.scaleBridge ? ACTION_EXIT_SCALE_GAIN : 0;

  if (!(elapsedMs >= profile.holdMs)) {
    // NaN·음수도 여기로 떨어진다 — 마지막 프레임 유지가 언제나 안전한 기본값이다.
    // holdMs 가 0 이면(seam-aligned) 이 분기는 사실상 지나가지 않는다.
    return {
      phase: "hold",
      actionAlpha: 1,
      actionScale: 1,
      idleAlpha: 0,
      idleScale: scale,
    };
  }

  // crossfadeMs 가 0 이면(immediate) 즉시 done — 0 나누기를 피한다.
  const q = profile.crossfadeMs > 0 ? (elapsedMs - profile.holdMs) / profile.crossfadeMs : 1;
  if (q >= 1) {
    return {
      phase: "done",
      actionAlpha: 0,
      actionScale: 1 + exitGain,
      idleAlpha: 1,
      idleScale: 1,
    };
  }

  return {
    phase: "crossfade",
    // 선형 교차 디졸브. 지각적 작업은 배율 이징이 담당하므로 알파까지 이징하면
    // 한쪽이 일찍 지배해 hold 가 짧아진 것처럼 보인다.
    actionAlpha: 1 - q,
    actionScale: 1 + exitGain * q,
    idleAlpha: q,
    // 감속하며 원래 크기로. 이게 "컷"을 "물러남"으로 바꾸는 핵심이다.
    // scaleBridge=false 면 scale===1 이라 이 항은 항상 1 이다.
    idleScale: scale + (1 - scale) * easeOutCubic(q),
  };
}
