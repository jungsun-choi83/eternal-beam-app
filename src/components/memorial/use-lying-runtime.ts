"use client";

import { useEffect, useMemo, useRef } from "react";

import {
  LyingRuntimeController,
  transitionSetReady,
  type PoseTransitionSources,
} from "@/lib/lying-runtime";
import type { PetRuntimeTrigger, RuntimeEventId } from "@/lib/pet-runtime-events";

/**
 * LyingRuntimeController 의 React 바인딩 — 로직은 전부 lib/lying-runtime 에
 * 있고, 여기는 수명 관리와 ref 배선만 한다.
 *
 * 발화는 플레이어 트리거 ref 를 **호출 시점에** 읽는다: 컨트롤러는 화면보다
 * 오래 살지 않고, 트리거는 소스가 생기고 사라질 때 null 이 됐다 돌아온다.
 */
export function useLyingRuntime(params: {
  triggerRef: React.MutableRefObject<PetRuntimeTrigger | null>;
  transitionSources: PoseTransitionSources;
}) {
  const { triggerRef, transitionSources } = params;

  const controller = useMemo(
    () =>
      new LyingRuntimeController({
        fire: (id: RuntimeEventId) => triggerRef.current?.(id),
      }),
    // triggerRef 는 ref 라 안정적이다 — 컨트롤러는 화면당 하나다.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const ready = transitionSetReady(transitionSources);
  useEffect(() => {
    controller.setTransitionReadiness(ready);
  }, [controller, ready]);

  useEffect(() => () => controller.dispose(), [controller]);

  const apiRef = useRef({
    dispatchUserAction: (id: RuntimeEventId) => controller.dispatchUserAction(id),
    notifyActivity: () => controller.notifyActivity(),
    onRuntimeEventChange: (id: RuntimeEventId | null) => controller.onEventChange(id),
  });
  return apiRef.current;
}
