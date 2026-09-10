/**
 * LYING 상태 흐름의 최소 런타임 (2026-09-08).
 *
 *   HOME(STANDING) ── 45~90s 무활동 ──▶ LIE_DOWN ──체인──▶ LIE_IDLE(누운 홈)
 *   LYING 중 서 있는 액션 요청 ──▶ 요청 기억 ─▶ STAND_UP ─▶ 홈 도착 ─▶ 기억한 액션
 *
 * ── 왜 프레임워크 없는 컨트롤러인가 ─────────────────────────────────────────
 * 타이머·대기 액션·자세 판정이 얽힌 규칙은 React 렌더 주기와 분리해야 테스트
 * 가능하다. 이 클래스는 타이머/난수/발화를 전부 주입받는 순수 조정자이고,
 * use-lying-runtime 훅이 React 에 바인딩만 한다.
 *
 * ── 자세는 여기서도 파생이다 ────────────────────────────────────────────────
 * 컨트롤러는 자기 자세 스토어를 두지 않는다. 플레이어가 알려 주는 "지금 재생
 * 중인 이벤트"(onEventChange) 에서 poseForCurrentEvent 로 계산한다 — 체인
 * 폴백(LIE_IDLE 소스 소멸 → BREATH 복귀) 같은 예외에서도 거짓말하지 않는다.
 *
 * ── 무엇을 하지 않는가 ─────────────────────────────────────────────────────
 * 자산 생성 없음. 적격성/소스/우선순위 판정 없음 — 발화는 전부 기존
 * decideTrigger 를 다시 통과한다. 이 모듈이 틀려도 최악은 "발화가 거절됨"이다.
 */

import {
  RUNTIME_EVENTS,
  poseForCurrentEvent,
  type RuntimeEventId,
} from "./pet-runtime-events.ts";

/** 무활동 → LIE_DOWN 대기 구간 (ms). 매번 이 사이에서 균등 난수로 뽑는다. */
export const LIE_DOWN_INACTIVITY_MIN_MS = 45_000;
export const LIE_DOWN_INACTIVITY_MAX_MS = 90_000;

/** 45~90s 균등 난수. 고정 주기는 기계처럼 보인다 — 호흡 리듬과 같은 원리다. */
export function pickLieDownDelayMs(random: () => number = Math.random): number {
  const span = LIE_DOWN_INACTIVITY_MAX_MS - LIE_DOWN_INACTIVITY_MIN_MS;
  return Math.round(LIE_DOWN_INACTIVITY_MIN_MS + random() * span);
}

/** 전이 세트 소스 표 — 셋 다 있어야 자발적 눕기가 허용된다. */
export type PoseTransitionSources = Partial<
  Record<"LIE_DOWN" | "LIE_IDLE" | "STAND_UP", string | null | undefined>
>;

/**
 * 셋 다 READY(소스 존재)인가. 하나라도 없으면 눕지 않는다 — LIE_DOWN 만 있고
 * STAND_UP 이 없으면 펫이 누운 채 영영 못 일어나고, LIE_IDLE 이 없으면 체인이
 * BREATH 폴백으로 떨어져 눕자마자 벌떡 일어난 것처럼 보인다.
 */
export function transitionSetReady(sources: PoseTransitionSources): boolean {
  return (["LIE_DOWN", "LIE_IDLE", "STAND_UP"] as const).every((id) => {
    const src = sources[id];
    return typeof src === "string" && src.trim().length > 0;
  });
}

export type UserActionPlan =
  | { type: "direct" }
  | { type: "wake-first"; wake: "STAND_UP" }
  | { type: "blocked"; reason: "cannot-wake" };

/**
 * 사용자/디바이스 액션 요청의 처리 계획.
 *
 * LYING 중 서 있는 자세가 필요한 요청 → STAND_UP 먼저 (wake-first).
 * PET_HEAD 도 여기 포함된다 — 현재 런타임 규칙(requiredPose 기본 STANDING)이
 * 누운 자세의 직접 재생을 지원하지 않기 때문이다. 클립 자체가 선 자세에서
 * 시작하므로 직접 틀면 포즈 점프가 난다. 누운 펫 쓰다듬기 클립이 생기면
 * PET_HEAD 정의에 requiredPose 분기를 더하는 것으로 충분하다.
 */
export function planUserAction(
  requestedId: RuntimeEventId,
  currentEventId: RuntimeEventId | null,
  canWake: boolean
): UserActionPlan {
  const pose = poseForCurrentEvent(currentEventId);
  const required = RUNTIME_EVENTS[requestedId]?.requiredPose ?? "STANDING";
  if (pose === "LYING" && required === "STANDING") {
    return canWake ? { type: "wake-first", wake: "STAND_UP" } : { type: "blocked", reason: "cannot-wake" };
  }
  return { type: "direct" };
}

export interface LyingRuntimeDeps {
  /** 플레이어 트리거로 발화 — decideTrigger 가 다시 판정한다. */
  fire: (id: RuntimeEventId) => void;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
}

export class LyingRuntimeController {
  private deps: Required<LyingRuntimeDeps>;
  private currentEventId: RuntimeEventId | null = null;
  private pending: RuntimeEventId | null = null;
  private ready = false;
  private timer: unknown = null;
  private disposed = false;

  constructor(deps: LyingRuntimeDeps) {
    this.deps = {
      fire: deps.fire,
      random: deps.random ?? Math.random,
      setTimer: deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms)),
      clearTimer: deps.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>)),
    };
  }

  /** 전이 세트 READY 여부가 바뀔 때 (소스/적격성 변동). */
  setTransitionReadiness(ready: boolean): void {
    this.ready = ready;
    if (!ready) this.pending = null; // 깨울 수 없게 됐다 — 낡은 의도를 버린다
    this.rearm();
  }

  /** 의미 있는 사용자/디바이스 상호작용 — 무활동 타이머를 처음부터 다시 잰다. */
  notifyActivity(): void {
    this.rearm();
  }

  /**
   * 플레이어의 현재 이벤트 변경 통지. null = 홈(BREATHING) 도착.
   * 홈 도착 시 기억해 둔 액션이 있으면 그것을 발화한다 (STAND_UP 완료 후).
   */
  onEventChange(currentEventId: RuntimeEventId | null): void {
    this.currentEventId = currentEventId;
    if (currentEventId === null && this.pending) {
      const next = this.pending;
      this.pending = null;
      this.deps.fire(next);
      // fire 가 수락되면 곧 onEventChange(next) 가 와서 타이머를 다시 멈춘다.
    }
    this.rearm();
  }

  /**
   * 사용자/디바이스가 유발한 액션 (탭·센서). 자발 스케줄러는 여기로 오지
   * 않는다 — 깜빡임이 누운 펫을 일으켜 세우면 안 되기 때문이다.
   */
  dispatchUserAction(requestedId: RuntimeEventId): void {
    const plan = planUserAction(requestedId, this.currentEventId, this.ready);
    if (plan.type === "blocked") return; // STAND_UP 없이는 깨울 수 없다
    if (plan.type === "wake-first") {
      this.pending = requestedId; // 최신 요청이 이긴다
      this.deps.fire(plan.wake);
      return;
    }
    this.notifyActivity();
    this.deps.fire(requestedId);
  }

  dispose(): void {
    this.disposed = true;
    this.cancelTimer();
  }

  private cancelTimer(): void {
    if (this.timer != null) {
      this.deps.clearTimer(this.timer);
      this.timer = null;
    }
  }

  /**
   * 무활동 타이머 재장전. 조건: 전이 세트 READY ∧ 홈(아무것도 재생 중 아님)
   * ∧ 대기 액션 없음. 조건이 깨지면 타이머도 없다 — LYING 중 이중 눕기나
   * 액션 재생 중 눕기가 구조적으로 불가능하다.
   */
  private rearm(): void {
    this.cancelTimer();
    if (this.disposed || !this.ready || this.currentEventId !== null || this.pending) return;
    this.timer = this.deps.setTimer(() => {
      this.timer = null;
      // 발화 직전 조건 재확인 — 타이머가 도는 동안 세상이 변했을 수 있다.
      if (this.disposed || !this.ready || this.currentEventId !== null || this.pending) return;
      this.deps.fire("LIE_DOWN");
    }, pickLieDownDelayMs(this.deps.random));
  }
}
