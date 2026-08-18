/**
 * 자발적 아이들 이벤트 스케줄링 — **순수 선택/타이밍 로직**.
 *
 *   BREATHING → 랜덤 정적 구간 → 이벤트 선택 → 1회 재생 → 랜덤 쿨다운 → 반복
 *
 * ── 왜 플레이어 밖에 있나 ────────────────────────────────────────────────────
 * idle-loop-video.tsx 는 "무엇을 언제 틀지" 결정하지 않는다. 트리거를 받아
 * 재생·전환만 한다. 스케줄러를 플레이어 안에 넣으면 그 경계가 무너지고,
 * "플레이어는 스스로 재생을 시작하지 않는다"는 회귀 가드도 못 쓰게 된다.
 * 자발적 트리거와 수동 트리거는 **완전히 같은 진입점**(trigger(id))을 쓴다.
 *
 * ── 후보의 출처 ──────────────────────────────────────────────────────────────
 * registeredIdleEvents() 하나뿐이다. 목록을 손으로 적지 않는다 — COME_CLOSER 를
 * 손으로 제외하는 코드가 생기는 순간, 액션이 늘었을 때 빠뜨리게 되고 펫이 혼자
 * 사용자에게 다가온다. kind 로 거르면 그런 실수가 구조적으로 불가능하다.
 *
 * 순수 모듈이다(DOM/React/타이머 없음) — `npm test` 가 그대로 로드한다.
 * 실제 타이머와 트리거 호출은 components/memorial/use-idle-event-scheduler.ts.
 */

import {
  type RuntimeEventDef,
  type RuntimeEventId,
  registeredIdleEvents,
} from "./pet-runtime-events.ts";

// ── 튜닝 상수 ────────────────────────────────────────────────────────────────
// 전부 여기 모아 둔다. 페이싱이 어색하면 이 숫자들만 만지면 된다.

/** 이벤트를 노릴 때까지의 조용한 구간(최소). */
export const IDLE_QUIET_MIN_MS = 7_000;
/** 이벤트를 노릴 때까지의 조용한 구간(최대). */
export const IDLE_QUIET_MAX_MS = 16_000;

/** 이벤트가 BREATHING 으로 돌아온 뒤의 쿨다운(최소). */
export const IDLE_COOLDOWN_MIN_MS = 3_500;
/** 이벤트가 BREATHING 으로 돌아온 뒤의 쿨다운(최대). */
export const IDLE_COOLDOWN_MAX_MS = 9_000;

/**
 * 트리거를 보낸 뒤 "정말 재생됐는지" 확인하는 유예.
 *
 * 필요한 이유: 트리거가 거절될 수 있다(더블탭과 같은 틱에 걸리는 등). 거절되면
 * 재생 시작/종료 알림이 오지 않으므로, 이 타이머가 없으면 스케줄러가 아무 타이머도
 * 걸지 않은 채 영영 멈춘다. 이음매 대기 상한보다 넉넉하게 잡는다.
 */
export const IDLE_TRIGGER_VERIFY_MS = 4_500;

/** 가중치를 지정하지 않은 이벤트의 기본값. */
export const DEFAULT_SPONTANEOUS_WEIGHT = 1;

// ── 후보 선정 ────────────────────────────────────────────────────────────────

/**
 * 지금 자발적으로 재생할 수 있는 아이들 이벤트.
 *
 * 조건 세 가지를 모두 만족해야 한다:
 *   1) 등록됨      — registeredIdleEvents() (= kind IDLE_EVENT, 정의 존재)
 *   2) 소스 있음   — 자산이 실제로 붙어 있다
 *   3) 가중치 > 0  — 0 으로 두면 수동 트리거만 남기고 자발적 선택에서 뺄 수 있다
 *
 * 액션(COME_CLOSER)은 1번에서 이미 걸러진다 — 여기에 별도 예외 처리가 없다.
 */
export function eligibleIdleEvents(
  availableIds: readonly RuntimeEventId[]
): RuntimeEventDef[] {
  const available = new Set(availableIds);
  return registeredIdleEvents().filter(
    (def) => available.has(def.id) && weightOf(def) > 0
  );
}

export function weightOf(def: RuntimeEventDef): number {
  const w = def.spontaneousWeight;
  return typeof w === "number" && Number.isFinite(w) && w > 0
    ? w
    : def.spontaneousWeight === 0
      ? 0
      : DEFAULT_SPONTANEOUS_WEIGHT;
}

/**
 * 다음에 재생할 이벤트를 고른다. 후보가 없으면 null.
 *
 * @param candidates  eligibleIdleEvents() 결과
 * @param lastPlayedId  직전에 재생한 이벤트 — 대안이 있으면 연속 반복을 피한다
 * @param random  0..1 난수. 테스트에서 결정적으로 만들기 위해 주입받는다.
 */
export function selectIdleEvent(
  candidates: readonly RuntimeEventDef[],
  lastPlayedId: RuntimeEventId | null,
  random: () => number
): RuntimeEventId | null {
  if (candidates.length === 0) return null;

  // 연속 반복 회피 — **대안이 있을 때만**. 후보가 하나뿐이면 그대로 다시 쓴다
  // (안 그러면 이벤트가 하나만 있는 펫에서 아무것도 재생되지 않는다).
  let pool = candidates;
  if (lastPlayedId !== null && candidates.length > 1) {
    const without = candidates.filter((d) => d.id !== lastPlayedId);
    if (without.length > 0) pool = without;
  }

  const total = pool.reduce((sum, d) => sum + weightOf(d), 0);
  if (total <= 0) return null;

  let roll = clamp01(random()) * total;
  for (const def of pool) {
    roll -= weightOf(def);
    if (roll < 0) return def.id;
  }
  // 부동소수 오차로 여기 오면 마지막 후보.
  return pool[pool.length - 1].id;
}

// ── 타이밍 ───────────────────────────────────────────────────────────────────

/** [min, max] 사이의 지연(ms). min > max 면 min 을 쓴다(설정 실수 방어). */
export function randomDelayMs(minMs: number, maxMs: number, random: () => number): number {
  if (!Number.isFinite(minMs) || minMs < 0) return 0;
  if (!Number.isFinite(maxMs) || maxMs <= minMs) return minMs;
  return Math.round(minMs + clamp01(random()) * (maxMs - minMs));
}

export function nextQuietDelayMs(random: () => number): number {
  return randomDelayMs(IDLE_QUIET_MIN_MS, IDLE_QUIET_MAX_MS, random);
}

export function nextCooldownDelayMs(random: () => number): number {
  return randomDelayMs(IDLE_COOLDOWN_MIN_MS, IDLE_COOLDOWN_MAX_MS, random);
}

function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.min(1, Math.max(0, v));
}
