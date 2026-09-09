"use client";

import { useCallback, useMemo } from "react";

import {
  eligibleBehaviorIds,
  eligibleSources,
  isBehaviorEligible,
} from "@/lib/behavior-library";
import { usePremiumAssetsContext } from "@/components/memorial/premium-assets-context";
import { COME_CLOSER_ACTION } from "@/lib/premium-unlock";

export interface BehaviorEligibility {
  /** 후보 목록 → 적격인 것만. 스케줄러의 availableIds 에 그대로 넘긴다. */
  filterIds: <T extends string>(ids: readonly T[]) => T[];
  /** 소스 표 → 적격인 것만. 플레이어의 idleEventSources 에 그대로 넘긴다. */
  filterSources: <T extends string>(
    sources: Partial<Record<T, string | null | undefined>>
  ) => Partial<Record<T, string>>;
  /** 더블탭이 실제로 동작해도 되는가. */
  comeCloserAllowed: boolean;
  /** 개별 판정이 필요할 때. */
  isEligible: (actionId: string) => boolean;
}

/**
 * 런타임 적격성 — **공유 자산 상태에서 파생만 한다**.
 *
 * 새 fetch 도 새 polling 도 만들지 않는다. PremiumAssetsProvider 가 이미 구독
 * 상태(entitled) · 자산 상태(READY) · 선호(ON/OFF)를 한 응답으로 들고 있으므로,
 * 세 조건이 **같은 시점**에서 나온다. 따로 조회하면 "구독은 만료로 읽었는데 자산은
 * 이전 응답"처럼 어긋난 조합이 만들어질 수 있다.
 *
 * 만료가 즉시 반영되는 것도 여기서 나온다: Provider 가 새 응답을 받는 순간
 * entitled=false 가 되고, 아래 파생값이 전부 비면서 스케줄러 후보와 소스가 함께
 * 사라진다. 스케줄러는 자기 규칙대로 "후보 없음"이 되어 조용히 멈춘다.
 */
export function useBehaviorEligibility(): BehaviorEligibility {
  const { assets } = usePremiumAssetsContext();

  const filterIds = useCallback(
    <T extends string>(ids: readonly T[]): T[] => eligibleBehaviorIds(ids, assets),
    [assets]
  );

  const filterSources = useCallback(
    <T extends string>(sources: Partial<Record<T, string | null | undefined>>) =>
      eligibleSources(sources, assets),
    [assets]
  );

  const isEligible = useCallback(
    (actionId: string) => isBehaviorEligible(actionId, assets),
    [assets]
  );

  const comeCloserAllowed = useMemo(
    () => isBehaviorEligible(COME_CLOSER_ACTION, assets),
    [assets]
  );

  return { filterIds, filterSources, comeCloserAllowed, isEligible };
}
