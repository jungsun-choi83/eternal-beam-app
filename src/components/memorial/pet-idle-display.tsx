"use client";

import { resolveIdleDisplaySource } from "@/lib/device-host-flags";
import { CutoutIdleMotion } from "@/components/memorial/cutout-idle-motion";
import { IdleLoopVideo } from "@/components/memorial/idle-loop-video";
import { shouldTransparentComposite } from "@/lib/baked-playback";
import type { IdleEvent, PetRuntimeTrigger } from "@/lib/pet-runtime-events";

interface PetIdleDisplayProps {
  idleVideoUrl?: string | null;
  /** COME_CLOSER 등 1회 재생 프리미엄 액션. 없으면 기존 BREATH 전용 동작 그대로. */
  comeCloserVideoUrl?: string | null;
  /**
   * 아이들 이벤트 소스 표 (**현재는 개발용 수동 트리거 전용**).
   *
   * 이벤트별 prop 을 하나씩 늘리지 않는다 — 4종까지 가면 prop 4개와 배선 4벌이
   * 되고, 하나만 빠뜨려도 조용히 재생되지 않는다. 등록된 id 를 키로 쓰는 표 하나로
   * 받는다. 값이 없는 이벤트는 <video> 자체가 마운트되지 않는다.
   *
   * 자발적 스케줄링은 없다.
   */
  idleEventSources?: Partial<Record<IdleEvent, string | null>>;
  /** 더블탭 등에서 호출할 액션 트리거 핸들. trigger("COME_CLOSER") 처럼 쓴다. */
  actionTriggerRef?: React.MutableRefObject<PetRuntimeTrigger | null>;
  onActionStateChange?: (playing: boolean) => void;
  cutoutUrl?: string | null;
  className?: string;
  style?: React.CSSProperties;
  preload?: "none" | "metadata" | "auto";
  allowDemoFallback?: boolean;
  /**
   * 이 자산의 배경이 이미 구워져 있는가 (Phase 19).
   * 기본 false = 레거시 — 지금까지의 동작(블랙키 제거)이 그대로 유지된다.
   */
  backgroundBaked?: boolean;
  /** 영상 경로에서만 호출됨 — 클립 하단 빈 배경 비율(0~1) 1회 통보. */
  onFeetMarginChange?: (bottomMargin: number) => void;
}

/** idle mp4(데모 목업 포함) 또는 cutout 정적 — 데모 off일 때만 정적 */
export function PetIdleDisplay({
  idleVideoUrl,
  comeCloserVideoUrl,
  idleEventSources,
  actionTriggerRef,
  onActionStateChange,
  cutoutUrl,
  className,
  style,
  preload = "metadata",
  allowDemoFallback,
  onFeetMarginChange,
  backgroundBaked = false,
}: PetIdleDisplayProps) {
  const display = resolveIdleDisplaySource(idleVideoUrl, cutoutUrl, {
    allowDemoFallback,
  });
  if (!display) return null;

  if (display.mode === "video") {
    return (
      <IdleLoopVideo
        src={display.src}
        // 런타임 이벤트 소스 표 — 액션이든 아이들 이벤트든 여기 항목이 하나 는다.
        eventSources={{
          ...idleEventSources,
          COME_CLOSER: comeCloserVideoUrl ?? null,
        }}
        actionTriggerRef={actionTriggerRef}
        onActionStateChange={onActionStateChange}
        className={className}
        style={style}
        preload={preload}
        onFeetMarginChange={onFeetMarginChange}
        // 배경이 구워진 자산은 **키잉하지 않는다.** 하면 장면의 어두운 픽셀이
        // 뚫리고 그 구멍으로 뒤 배경이 비친다(배경 이중 적용).
        transparentComposite={shouldTransparentComposite({ backgroundBaked })}
      />
    );
  }

  return <CutoutIdleMotion src={display.src} className={className} style={style} />;
}
