import type { StoredPipeline } from "@/components/memorial/ai-processing-screen";

/**
 * My Library 가 테마 선택·미리보기에 내려주는 발행 메타데이터.
 *
 * 부모(MyLibraryScreen)가 모션을 고른 시점에 **동기적으로** 만들어 props 로
 * 흘려보낸다 — 자식이 sessionStorage 를 읽는 effect 가 돌 때까지 기다리지
 * 않는다. 그래야 "라이브러리 모드인가"가 첫 렌더부터 확정된다.
 */
export interface LibraryPublication {
  petId: string;
  idleVideoUrl: string;
  deliveryFormat: string | null;
  backgroundBaked: boolean;
  /** 발행된 모션 id (예: "BREATHING") — 요약 카드 표시용, 선택값. */
  motionId?: string;
}

/**
 * 저장된 파이프라인 위에 라이브러리 발행 메타데이터를 덮어쓴다.
 *
 * sessionStorage 쓰기가 조용히 실패했거나(쿼터), 남아 있는 값이 이전 업로드
 * 세션의 잔재이거나, 아직 아무것도 읽지 못했어도(첫 렌더) — 이 함수를 거치면
 * pet_id/idle_video_url/delivery_format/background_baked 는 항상 발행
 * 메타데이터가 정본이다. isLibraryFlow 가 아니면 손대지 않고 그대로 돌려준다.
 */
export function applyLibraryOverride(
  pipeline: StoredPipeline | null,
  isLibraryFlow: boolean,
  publication: LibraryPublication | null | undefined
): StoredPipeline | null {
  if (!isLibraryFlow || !publication) return pipeline;
  const base: StoredPipeline = pipeline ?? {
    content_id: publication.petId,
    cutout_display_url: "",
    dog_only_nobg_url: "",
    idle_video_url: "",
    action_video_url: "",
  };
  return {
    ...base,
    idle_video_url: publication.idleVideoUrl,
    delivery_format: publication.deliveryFormat,
    background_baked: publication.backgroundBaked,
    generation_source: "library",
  };
}
