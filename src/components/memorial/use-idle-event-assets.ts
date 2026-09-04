"use client";

import { useMemo } from "react";

import type { StoredPipeline } from "@/components/memorial/ai-processing-screen";
import { usePremiumAssetsContext } from "@/components/memorial/premium-assets-context";
import { registeredIdleEvents, type IdleEvent } from "@/lib/pet-runtime-events";

export interface IdleEventAssetsOptions {
  pipeline: StoredPipeline | null;
  /**
   * **실제 BREATH 자산이 있는가** (hasRealIdleVideo). false 면 아무것도 노출하지
   * 않는다.
   *
   * 데모/폴백 mp4 를 근거로 켜면 안 된다: 이벤트 클립의 seam-aligned 복귀는
   * "BREATH 의 휴지 자세와 같다"는 전제 위에 서 있는데, 데모 클립은 다른 개다 —
   * 이음매가 그대로 보인다.
   */
  enabled: boolean;
}

export interface IdleEventAssets {
  /** 이벤트 id → 승격된 클립 URL (호출 시점 재서명). 아직 없는 이벤트는 키가 없다. */
  urls: Partial<Record<IdleEvent, string>>;
  /**
   * 이벤트 id → 명시 전달 포맷 (Phase 7I.1). "packed_alpha" = 새 시스템 파생물,
   * null = 레거시(기존 blackkey/휴리스틱 규칙). URL 이 있는 이벤트에만 키가 있다.
   */
  formats: Partial<Record<IdleEvent, string | null>>;
  /** URL 이 확보된(READY) 이벤트 id 들 — 스케줄러의 후보 목록. */
  availableIds: IdleEvent[];
}

const EMPTY: IdleEventAssets = { urls: {}, formats: {}, availableIds: [] };

/**
 * 아이들 이벤트 자산 **발견(discovery)** — 조회 전용.
 *
 * ⚠️ 이 훅은 **절대 새 유료 생성을 시작하지 않는다.** 새 생성은
 * lib/premium-assets.ts 의 purchasePremium() 으로만 일어난다 — 사용자 조작에서
 * 호출되며, effect/마운트/폴링에서는 부르지 않는다.
 *
 * ── Phase 7I.1: 발견원이 인증 컨텍스트 하나로 통일됐다 ──────────────────────
 * 예전에는 무과금 개발 엔드포인트(lookupIdleEventAsset, DEV 전용)를 직접
 * 폴링했다 — 프로덕션 빌드에서는 게이트에 막혀 **아무것도 발견되지 않았고**,
 * 구매한 자산이 영영 재생되지 않았다.
 *
 * 이제 PremiumAssetsProvider 의 인증 발견(GET /premium/assets)을 그대로 읽는다:
 *   * URL 은 서버가 호출 시점에 재서명한 값이다 (만료 서명 없음)
 *   * 모션마다 명시 delivery_format 이 실린다 (BREATH 모드에서 파생하지 않는다)
 *   * 폴링·가시성 재확인·구독 만료 반영은 Provider 가 한 곳에서 한다
 *
 * 자발적 스케줄링도 여기 없다. 이 훅은 "무엇이 재생 가능한가"만 답하고,
 * "언제 재생하는가"는 useIdleEventScheduler 가 정한다.
 */
export function useIdleEventAssets({
  pipeline,
  enabled,
}: IdleEventAssetsOptions): IdleEventAssets {
  const { assets } = usePremiumAssetsContext();

  // 이벤트마다 state·effect 를 하나씩 늘리지 않는다. 등록된 아이들 이벤트를
  // 순회하므로, 새 이벤트를 레지스트리에 추가하면 여기 배선은 그대로 따라온다.
  return useMemo(() => {
    if (!enabled || !pipeline || !assets) return EMPTY;
    const urls: Partial<Record<IdleEvent, string>> = {};
    const formats: Partial<Record<IdleEvent, string | null>> = {};
    for (const def of registeredIdleEvents()) {
      const eventId = def.id as IdleEvent;
      const entry = assets.readyAssets[eventId];
      const url = entry?.url;
      if (typeof url === "string" && url.length > 0) {
        urls[eventId] = url;
        formats[eventId] = entry?.deliveryFormat ?? null;
      }
    }
    const availableIds = Object.keys(urls) as IdleEvent[];
    return { urls, formats, availableIds };
  }, [enabled, pipeline, assets]);
}
