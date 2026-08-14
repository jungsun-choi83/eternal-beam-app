"use client";

import { useCallback, useEffect, useRef } from "react";

import {
  IDLE_TRIGGER_VERIFY_MS,
  eligibleIdleEvents,
  nextCooldownDelayMs,
  nextQuietDelayMs,
  selectIdleEvent,
} from "@/lib/idle-event-scheduler";
import type { PetRuntimeTrigger, RuntimeEventId } from "@/lib/pet-runtime-events";

export interface IdleEventSchedulerOptions {
  /** false 면 아무 타이머도 걸지 않는다. */
  enabled: boolean;
  /** 소스가 확보된 이벤트 id 들. 여기 없는 이벤트는 후보가 되지 않는다. */
  availableIds: readonly RuntimeEventId[];
  /** 플레이어가 넘겨준 트리거 핸들. 수동 트리거와 **같은 진입점**이다. */
  triggerRef: React.MutableRefObject<PetRuntimeTrigger | null>;
}

export interface IdleEventScheduler {
  /**
   * 플레이어의 onActionStateChange 에 그대로 연결한다.
   * 스케줄러가 "지금 뭔가 재생 중인가"를 아는 유일한 신호다.
   */
  onPlaybackStateChange: (playing: boolean) => void;
}

/**
 * 자발적 아이들 이벤트 스케줄러 (타이머 + 트리거).
 *
 * 상태 기계:
 *   armed(quiet)  ── 타이머 만료 ─→ select → trigger(id) → awaiting
 *   awaiting      ── playing=true ─→ busy        (재생이 실제로 시작됨)
 *                 └─ 검증 타임아웃 ─→ armed      (거절됐다 — 다시 노린다)
 *   busy          ── playing=false ─→ armed(cooldown)
 *
 * 불변식:
 *   * **타이머는 항상 최대 한 개.** 겹침/큐잉이 구조적으로 불가능하다.
 *   * playing=true 면 무조건 타이머를 버린다 — COME_CLOSER 든 아이들이든,
 *     뭔가 재생 중일 때 스케줄러는 아무것도 하지 않는다.
 *   * COME_CLOSER 가 끝나도 **미뤄 둔 이벤트를 재생하지 않는다**. 새 쿨다운으로
 *     처음부터 다시 시작한다 — 큐잉 금지 요구사항이 여기서 지켜진다.
 */
export function useIdleEventScheduler({
  enabled,
  availableIds,
  triggerRef,
}: IdleEventSchedulerOptions): IdleEventScheduler {
  const timerRef = useRef<number | null>(null);
  /** 직전에 **실제로 재생된** 이벤트. 연속 반복 회피용. */
  const lastPlayedRef = useRef<RuntimeEventId | null>(null);
  /** 트리거를 보내고 재생 시작을 기다리는 중인 이벤트. */
  const awaitingRef = useRef<RuntimeEventId | null>(null);
  /** 플레이어가 뭔가 재생 중인가 (액션 포함). */
  const busyRef = useRef(false);

  // 매 렌더 바뀌는 값들은 ref 로 읽는다 — 타이머를 다시 걸지 않기 위해서.
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const availableRef = useRef(availableIds);
  availableRef.current = availableIds;

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  // arm 과 fire 는 서로를 부른다(예약 → 발화 → 재예약). ref 로 한 단계 끊어
  // 초기화 순서 문제와 의존성 순환을 피한다.
  const fireRef = useRef<() => void>(() => {});

  /** 다음 기회를 예약한다. 항상 기존 타이머를 먼저 버린다. */
  const arm = useCallback(
    (delayMs: number) => {
      clearTimer();
      if (!enabledRef.current) return;
      if (busyRef.current) return; // 재생 중에는 노리지 않는다
      timerRef.current = window.setTimeout(() => {
        timerRef.current = null;
        fireRef.current();
      }, delayMs);
    },
    [clearTimer]
  );

  /** 기회가 왔다 — 후보를 골라 트리거한다. */
  const fire = useCallback(() => {
    if (!enabledRef.current || busyRef.current) return;
    const trigger = triggerRef.current;
    if (!trigger) {
      // 플레이어가 아직 준비되지 않았다(BREATH 영상 없음 등). 조용히 다시 노린다.
      arm(nextQuietDelayMs(Math.random));
      return;
    }

    const candidates = eligibleIdleEvents(availableRef.current);
    const picked = selectIdleEvent(candidates, lastPlayedRef.current, Math.random);
    if (!picked) {
      // 자산이 하나도 없다 — 오류 루프를 만들지 않고 BREATHING 에 머문다.
      arm(nextQuietDelayMs(Math.random));
      return;
    }

    awaitingRef.current = picked;
    if (import.meta.env.DEV) console.info(`[idle-scheduler] 자발적 트리거 → ${picked}`);
    trigger(picked);

    // 트리거가 거절될 수 있다(예: 같은 틱에 더블탭). 그러면 재생 알림이 오지
    // 않으므로, 이 검증 타이머가 없으면 스케줄러가 영영 멈춘다.
    clearTimer();
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      if (busyRef.current) return; // 실제로 재생됐다 — 종료 알림이 처리한다
      if (import.meta.env.DEV) {
        console.info(`[idle-scheduler] ${awaitingRef.current} 재생되지 않음 — 재예약`);
      }
      awaitingRef.current = null;
      arm(nextQuietDelayMs(Math.random));
    }, IDLE_TRIGGER_VERIFY_MS);
  }, [arm, clearTimer, triggerRef]);
  fireRef.current = fire;

  const onPlaybackStateChange = useCallback(
    (playing: boolean) => {
      busyRef.current = playing;
      if (playing) {
        // 무엇이 재생되든(COME_CLOSER 포함) 스케줄러는 손을 뗀다.
        // 예약돼 있던 기회는 **버린다** — 뒤에 쌓아 두지 않는다.
        clearTimer();
        if (awaitingRef.current) {
          lastPlayedRef.current = awaitingRef.current;
          awaitingRef.current = null;
        }
        return;
      }
      // BREATHING 으로 돌아왔다 — 쿨다운 후 다시 노린다.
      awaitingRef.current = null;
      arm(nextCooldownDelayMs(Math.random));
    },
    [arm, clearTimer]
  );

  // 활성화/자산 목록이 바뀌면 처음부터 다시 예약한다.
  // availableIds 는 매 렌더 새 배열일 수 있으므로 **내용 기반 키**로 비교한다 —
  // 배열 신원으로 비교하면 렌더마다 타이머가 리셋돼 이벤트가 영영 안 나온다.
  const availableKey = [...availableIds].sort().join("|");
  useEffect(() => {
    if (!enabled) {
      clearTimer();
      return;
    }
    if (!busyRef.current) arm(nextQuietDelayMs(Math.random));
    return clearTimer;
  }, [enabled, availableKey, arm, clearTimer]);

  return { onPlaybackStateChange };
}
