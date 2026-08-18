"use client";

import { useEffect, useMemo, useState } from "react";

import type { StoredPipeline } from "@/components/memorial/ai-processing-screen";
import type { CutoutPipelineLike } from "@/lib/cutout-remote-asset";
import { ensureIdleEventAsset } from "@/lib/idle-event-dev-trigger";
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
 * 아이들 이벤트 자산 확보 (조회 + 필요 시 1회 생성 제출) — **개발 빌드 전용**.
 *
 * preview-screen 안에 인라인으로 있던 스윕을 그대로 끌어낸 것이다. 두 화면
 * (preview / memorial-device-play)이 **같은 구현**을 써야 한다: 이 루프는 유료
 * 생성을 제출하므로 사본이 갈라지면 한쪽에서만 중복 지출이 난다.
 *
 * DEV 게이트를 훅 안에 두는 이유: 게이트가 화면마다 흩어지면 한 곳을 빠뜨렸을 때
 * 프로덕션에서 조용히 생성이 시작된다. 지금 백엔드 엔드포인트도
 * ENABLE_DEV_PREMIUM_TRIGGER 로 잠겨 있어(과금 0) 프로덕션 활성화는 별도의
 * 권한/과금 결정을 필요로 한다 — 그 결정이 나기 전까지 두 화면이 함께 잠겨 있어야 한다.
 *
 * 자발적 스케줄링은 여기 **없다**. 이 훅은 "무엇이 재생 가능한가"만 답하고,
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
        const r = await ensureIdleEventAsset({
          userId,
          petId,
          eventId,
          // 전개(spread)로 넘기는 이유: CutoutPipelineLike 은
          // `[k: string]: unknown` 인덱스 시그니처를 요구하는데, TS 는 interface
          // (StoredPipeline)에는 암묵적 인덱스 시그니처를 주지 않아 그대로는
          // 대입되지 않는다. 전개하면 익명 객체 타입이 되어 요구를 만족한다 —
          // 캐스트 없이 타입이 맞는다. ensureIdleEventAsset 은 이 객체의 필드만
          // 읽고 신원(identity)에 의존하지 않으므로 얕은 복사로 충분하다.
          pipeline: { ...pipeline } satisfies CutoutPipelineLike,
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
