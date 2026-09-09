"use client";

import { useEffect, useMemo, useState } from "react";

import type { StoredPipeline } from "@/components/memorial/ai-processing-screen";
import { lookupIdleEventAsset } from "@/lib/idle-event-dev-trigger";
import { getEternalBeamPetId } from "@/lib/pet-identity";
import { getEternalBeamUserId } from "@/lib/eternal-beam-user";
import { registeredIdleEvents, type IdleEvent } from "@/lib/pet-runtime-events";

/**
 * 큐가 빠졌는지 다시 물어보는 주기.
 *
 * 짧으면 서버에 헛질문이 늘고, 길면 앞 작업이 끝난 뒤 다음 제출까지 놀게 된다.
 * 생성 자체가 분 단위라 20초면 충분하다.
 */
export const IDLE_ASSET_SWEEP_MS = 20_000;

export interface IdleEventAssetsOptions {
  pipeline: StoredPipeline | null;
  /**
   * **실제 BREATH 자산이 있는가** (hasRealIdleVideo). false 면 아무것도 하지 않는다.
   *
   * 데모/폴백 mp4 를 근거로 켜면 안 된다. 두 가지가 동시에 깨진다:
   *   1) BREATH 생성이 실패한 펫에게 4건의 유료 생성이 나간다
   *   2) 이벤트 클립의 seam-aligned 복귀는 "BREATH 의 휴지 자세와 같다"는 전제
   *      위에 서 있는데, 데모 클립은 다른 개다 — 이음매가 그대로 보인다
   */
  enabled: boolean;
}

export interface IdleEventAssets {
  /** 이벤트 id → 승격된 클립 URL. 아직 없는 이벤트는 키가 없다. */
  urls: Partial<Record<IdleEvent, string>>;
  /** URL 이 확보된(READY) 이벤트 id 들 — 스케줄러의 후보 목록. */
  availableIds: IdleEvent[];
}

/**
 * 아이들 이벤트 자산 **발견(discovery)** — 조회 전용.
 *
 * ⚠️ 이 훅은 **절대 새 유료 생성을 시작하지 않는다.**
 *
 * 예전에는 여기서 자산이 없으면 곧바로 생성을 제출했다. 무과금 개발 엔드포인트에서는
 * 편의였지만, 유료 모델에서는 **화면을 열었다는 이유로 결제가 일어나는** 것과 같다.
 * 사업 규칙상 아이들 번들은 1 크레딧짜리 구매이고, 구매는 사용자의 명시적 의사가
 * 있을 때만 일어나야 한다.
 *
 * 지금 이 훅이 하는 일은 셋뿐이다:
 *   * 승격된 자산을 조회한다 (GET)
 *   * 이미 진행 중인 작업이 끝날 때까지 폴링한다 (GET)
 *   * availableIds 를 노출한다 (스케줄러의 후보 목록)
 *
 * 새 생성은 lib/premium-assets.ts 의 purchasePremium() 으로만 일어난다 —
 * 사용자 조작에서 호출되며, effect/마운트/폴링에서는 부르지 않는다.
 *
 * 자발적 스케줄링도 여기 없다. 이 훅은 "무엇이 재생 가능한가"만 답하고,
 * "언제 재생하는가"는 useIdleEventScheduler 가 정한다.
 */
export function useIdleEventAssets({
  pipeline,
  enabled,
}: IdleEventAssetsOptions): IdleEventAssets {
  // 이벤트마다 state·effect 를 하나씩 늘리지 않는다. 등록된 아이들 이벤트를
  // 순회하므로, 새 이벤트를 레지스트리에 추가하면 여기 배선은 그대로 따라온다.
  const [urls, setUrls] = useState<Partial<Record<IdleEvent, string>>>({});

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    if (!pipeline) return;
    // 확인(confirm) 전에는 생성하지 않는다. BREATH 가 없으면 재생기가 마운트되지
    // 않으므로 만든 자산을 보여줄 수도 없고, 사용자가 되돌아가면 비용만 남는다.
    if (!enabled) return;
    let cancelled = false;
    const userId = getEternalBeamUserId();
    const petId = getEternalBeamPetId(pipeline.content_id);

    // 한 바퀴 = 아직 확보되지 않은 이벤트마다 ensure 한 번.
    //
    // 동시 제출 수는 **서버가** 막는다(generation_queue). 여기서 순차 루프를 돌지
    // 않는 이유가 그것이다 — 브라우저 큐는 탭을 두 개 열면 그대로 뚫린다. 프론트는
    // "이 펫 자산 좀 챙겨 줘"라고 반복해서 물을 뿐이고, 상한에 걸린 요청은
    // status=queued 로 조용히 되돌아온다(프로바이더 호출 없음).
    const sweep = async () => {
      let pending = false;
      for (const def of registeredIdleEvents()) {
        if (cancelled) return false;
        const eventId = def.id as IdleEvent;
        const r = await lookupIdleEventAsset({
          userId,
          petId,
          eventId,
          onState: (st) => console.info(`[${eventId}] asset state =`, st),
        });
        if (cancelled) return false;
        if (r.url) {
          setUrls((prev) =>
            prev[eventId] === r.url ? prev : { ...prev, [eventId]: r.url as string }
          );
        } else if (r.state === "queued" || r.state === "generating") {
          pending = true; // 아직 남았다 — 다음 바퀴에서 다시 물어본다
        }
      }
      return pending;
    };

    // 큐가 빠지는 속도에 맞춰 주기적으로 다시 훑는다. 남은 게 없으면 멈춘다.
    let timer: number | null = null;
    const run = () => {
      void sweep().then((pending) => {
        if (cancelled || !pending) return;
        timer = window.setTimeout(run, IDLE_ASSET_SWEEP_MS);
      });
    };
    run();

    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [pipeline, enabled]);

  // 자산이 하나도 없으면 후보가 비어 아무 일도 일어나지 않는다 — BREATHING 유지.
  const availableIds = useMemo(
    () =>
      Object.entries(urls)
        .filter(([, url]) => typeof url === "string" && url.length > 0)
        .map(([id]) => id as IdleEvent),
    [urls]
  );

  return { urls, availableIds };
}
